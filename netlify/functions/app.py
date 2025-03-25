from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from flask_sqlalchemy import SQLAlchemy
import tweepy
import os
from datetime import datetime, timedelta
from dotenv import load_dotenv
import serverless_wsgi

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', os.urandom(24))
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///work_time.db')
db = SQLAlchemy(app)

# Twitter API v2設定
CLIENT_ID = os.getenv('TWITTER_CLIENT_ID')
CLIENT_SECRET = os.getenv('TWITTER_CLIENT_SECRET')
CALLBACK_URL = os.environ.get('CALLBACK_URL', 'http://localhost:5000/callback')

# データベースモデル
class WorkSession(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(100), nullable=False)
    duration = db.Column(db.Integer, nullable=False)
    description = db.Column(db.String(280))
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

with app.app_context():
    db.create_all()

@app.route('/')
def index():
    if 'user_id' not in session:
        return render_template('login.html')
    return render_template('index.html')

@app.route('/login')
def login():
    oauth2_user_handler = tweepy.OAuth2UserHandler(
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        redirect_uri=CALLBACK_URL,
        scope=['tweet.read', 'tweet.write', 'users.read']
    )
    return redirect(oauth2_user_handler.get_authorization_url())

@app.route('/callback')
def callback():
    code = request.args.get('code')
    if not code:
        return redirect(url_for('index'))
    
    try:
        oauth2_user_handler = tweepy.OAuth2UserHandler(
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
            redirect_uri=CALLBACK_URL,
            scope=['tweet.read', 'tweet.write', 'users.read']
        )
        access_token = oauth2_user_handler.fetch_token(code)
        
        client = tweepy.Client(bearer_token=access_token['access_token'])
        me = client.get_me()
        
        session['user_id'] = me.data.id
        session['username'] = me.data.username
        session['access_token'] = access_token['access_token']
        
        return redirect(url_for('index'))
    except Exception as e:
        return f'Error! Failed to get access token: {str(e)}'

@app.route('/record', methods=['POST'])
def record_work():
    if 'user_id' not in session:
        return redirect(url_for('index'))
    
    duration = int(request.form.get('duration'))
    description = request.form.get('description')
    
    work_session = WorkSession(
        user_id=session['user_id'],
        duration=duration,
        description=description
    )
    db.session.add(work_session)
    db.session.commit()
    
    # ツイート投稿
    client = tweepy.Client(
        bearer_token=os.getenv('TWITTER_BEARER_TOKEN'),
        access_token=os.getenv('TWITTER_ACCESS_TOKEN'),
        access_token_secret=os.getenv('TWITTER_ACCESS_TOKEN_SECRET'),
        consumer_key=os.getenv('TWITTER_API_KEY'),
        consumer_secret=os.getenv('TWITTER_API_SECRET')
    )
    
    tweet_text = f"作業時間: {duration}分\n{description}"
    client.create_tweet(text=tweet_text)
    
    return redirect(url_for('index'))

@app.route('/stats')
def get_stats():
    if 'user_id' not in session:
        return redirect(url_for('index'))
    
    now = datetime.utcnow()
    week_ago = now - timedelta(days=7)
    work_sessions = WorkSession.query.filter(
        WorkSession.user_id == session['user_id'],
        WorkSession.timestamp >= week_ago
    ).all()
    
    total_time = sum(session.duration for session in work_sessions)
    daily_times = {}
    
    for session in work_sessions:
        day = session.timestamp.strftime('%Y-%m-%d')
        daily_times[day] = daily_times.get(day, 0) + session.duration
    
    return jsonify({
        'total_time': total_time,
        'daily_times': daily_times
    })

def handler(event, context):
    return serverless_wsgi.handle_request(app, event, context)
