from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from flask_sqlalchemy import SQLAlchemy
import os
from datetime import datetime, timedelta
import json
import re
import nltk
from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build

# NLTK初期化
try:
    nltk.data.find('tokenizers/punkt')
except LookupError:
    nltk.download('punkt')

load_dotenv()

app = Flask(__name__)
app.secret_key = os.urandom(24)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///work_time.db'
db = SQLAlchemy(app)

# Google Sheets API設定
SCOPES = ['https://www.googleapis.com/auth/spreadsheets.readonly']
SPREADSHEET_ID = os.getenv('GOOGLE_SPREADSHEET_ID')
RANGE_NAME = os.getenv('GOOGLE_SHEET_RANGE', 'A:D')  # デフォルトではA〜D列を読み込む

# データベースモデル
class WorkSession(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(100), nullable=False)
    duration = db.Column(db.Integer, nullable=False)  # 分単位
    description = db.Column(db.String(280))
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

with app.app_context():
    db.create_all()

def extract_time_from_text(text):
    """ツイートテキストから時間情報を抽出する"""
    # 「XX分」「XX時間」のパターンを検索
    minutes_pattern = r'(\d+)\s*分'
    hours_pattern = r'(\d+)\s*時間'
    
    minutes = 0
    
    # 分を抽出
    minutes_match = re.search(minutes_pattern, text)
    if minutes_match:
        minutes += int(minutes_match.group(1))
    
    # 時間を抽出して分に変換
    hours_match = re.search(hours_pattern, text)
    if hours_match:
        minutes += int(hours_match.group(1)) * 60
    
    return minutes

def get_sheets_data():
    """Google Sheetsからデータを取得"""
    try:
        # 環境変数からサービスアカウントJSONを取得
        credentials_json = os.getenv('GOOGLE_CREDENTIALS')
        if not credentials_json:
            return {"error": "Google認証情報が設定されていません"}
        
        credentials_dict = json.loads(credentials_json)
        credentials = service_account.Credentials.from_service_account_info(
            credentials_dict, scopes=SCOPES)
        
        service = build('sheets', 'v4', credentials=credentials)
        sheet = service.spreadsheets()
        result = sheet.values().get(spreadsheetId=SPREADSHEET_ID,
                                   range=RANGE_NAME).execute()
        
        values = result.get('values', [])
        
        if not values:
            return {"error": "データが見つかりません"}
        
        # データを処理して時間情報を抽出
        processed_data = []
        for row in values[1:]:  # ヘッダー行をスキップ
            if len(row) >= 2:  # 少なくとも日付とテキスト列があることを確認
                date_str = row[0]  # 日付
                tweet_text = row[1]  # ツイート内容
                
                # 時間情報を抽出
                duration = extract_time_from_text(tweet_text)
                
                if duration > 0:
                    processed_data.append({
                        'date': date_str,
                        'duration': duration,
                        'text': tweet_text
                    })
        
        return processed_data
    
    except Exception as e:
        return {"error": str(e)}

@app.route('/')
def index():
    # 簡易認証（本番環境では改善が必要）
    if 'user_id' not in session:
        session['user_id'] = 'default_user'
        session['username'] = 'default_user'
    return render_template('index.html')

@app.route('/record', methods=['POST'])
def record_work():
    if 'user_id' not in session:
        session['user_id'] = 'default_user'
    
    duration = int(request.form.get('duration'))
    description = request.form.get('description')
    
    work_session = WorkSession(
        user_id=session['user_id'],
        duration=duration,
        description=description
    )
    db.session.add(work_session)
    db.session.commit()
    
    return redirect(url_for('index'))

@app.route('/stats')
def get_stats():
    if 'user_id' not in session:
        session['user_id'] = 'default_user'
    
    # データソースを選択（クエリパラメータで指定可能）
    data_source = request.args.get('source', 'sheets')  # デフォルトはsheetsから
    
    if data_source == 'local':
        # ローカルDBからデータを取得
        now = datetime.utcnow()
        week_ago = now - timedelta(days=7)
        work_sessions = WorkSession.query.filter(
            WorkSession.user_id == session['user_id'],
            WorkSession.timestamp >= week_ago
        ).all()
        
        total_time = sum(session.duration for session in work_sessions)
        daily_times = {}
        daily_texts = {}
        
        for session in work_sessions:
            day = session.timestamp.strftime('%Y-%m-%d')
            daily_times[day] = daily_times.get(day, 0) + session.duration
            
            if day not in daily_texts:
                daily_texts[day] = []
            daily_texts[day].append(session.description)
        
        return jsonify({
            'total_time': total_time,
            'daily_times': daily_times,
            'daily_texts': daily_texts
        })
    
    else:
        # Google Sheetsからデータを取得
        sheets_data = get_sheets_data()
        
        if isinstance(sheets_data, dict) and 'error' in sheets_data:
            return jsonify(sheets_data)
        
        # 過去7日間のデータをフィルタリング
        now = datetime.utcnow()
        week_ago = now - timedelta(days=7)
        
        daily_times = {}
        daily_texts = {}
        total_time = 0
        
        for item in sheets_data:
            try:
                # 日付形式は実際のスプレッドシートに合わせて調整が必要
                date_obj = datetime.strptime(item['date'], '%Y/%m/%d %H:%M:%S')
                
                if date_obj >= week_ago:
                    day = date_obj.strftime('%Y-%m-%d')
                    daily_times[day] = daily_times.get(day, 0) + item['duration']
                    total_time += item['duration']
                    
                    if day not in daily_texts:
                        daily_texts[day] = []
                    daily_texts[day].append(item['text'])
            except ValueError:
                # 日付形式が異なる場合はスキップ
                continue
        
        return jsonify({
            'total_time': total_time,
            'daily_times': daily_times,
            'daily_texts': daily_texts
        })

@app.route('/test-sheets')
def test_sheets():
    """Sheetsからのデータ取得をテストするためのエンドポイント"""
    sheets_data = get_sheets_data()
    return jsonify(sheets_data)

if __name__ == '__main__':
    app.run(debug=True)
