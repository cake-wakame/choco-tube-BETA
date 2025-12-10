import os
import json
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import urllib.parse
import datetime
import random
import time
import tempfile
import subprocess
import re
from functools import lru_cache
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import Flask, render_template, request, jsonify, Response, redirect, url_for, session, send_file
from functools import wraps
import yt_dlp

app = Flask(__name__)
app.config['JSON_AS_ASCII'] = False
app.secret_key = os.environ.get('SESSION_SECRET', os.environ.get('SECRET_KEY', 'choco-tube-secret-key-2025'))

# セッションクッキーの設定（Render等のHTTPS環境で必要）
app.config['SESSION_COOKIE_SECURE'] = os.environ.get('RENDER', False) or os.environ.get('FLASK_ENV') == 'production'
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

PASSWORD = os.environ.get('APP_PASSWORD', 'choco')

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

YOUTUBE_API_KEY = os.environ.get('YOUTUBE_API_KEY', '')

EDU_VIDEO_API = "https://siawaseok.duckdns.org/api/video2/"
EDU_CONFIG_URL = "https://raw.githubusercontent.com/siawaseok3/wakame/master/video_config.json"
STREAM_API = "https://ytdl-0et1.onrender.com/stream/"
M3U8_API = "https://ytdl-0et1.onrender.com/m3u8/"

_edu_params_cache = {'params': None, 'timestamp': 0}
_trending_cache = {'data': None, 'timestamp': 0}
_thumbnail_cache = {}

http_session = requests.Session()
retry_strategy = Retry(total=2, backoff_factor=0.1, status_forcelist=[500, 502, 503, 504])
adapter = HTTPAdapter(max_retries=retry_strategy, pool_connections=20, pool_maxsize=20)
http_session.mount("http://", adapter)
http_session.mount("https://", adapter)

USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Safari/605.1.15',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:92.0) Gecko/20100101 Firefox/92.0',
]

INVIDIOUS_INSTANCES = [
    'https://inv.nadeko.net/',
    'https://invidious.f5.si/',
    'https://invidious.lunivers.trade/',
    'https://invidious.ducks.party/',
    'https://super8.absturztau.be/',
    'https://invidious.nikkosphere.com/',
    'https://yt.omada.cafe/',
    'https://iv.melmac.space/',
    'https://iv.duti.dev/',
]

def get_random_headers():
    return {
        'User-Agent': random.choice(USER_AGENTS)
    }

def get_edu_params():
    cache_duration = 300
    current_time = time.time()

    if _edu_params_cache['params'] and (current_time - _edu_params_cache['timestamp']) < cache_duration:
        return _edu_params_cache['params']

    try:
        res = http_session.get(EDU_CONFIG_URL, headers=get_random_headers(), timeout=3)
        res.raise_for_status()
        data = res.json()
        params = data.get('params', '')
        if params.startswith('?'):
            params = params[1:]
        params = params.replace('&amp;', '&')
        _edu_params_cache['params'] = params
        _edu_params_cache['timestamp'] = current_time
        return params
    except Exception as e:
        print(f"Failed to fetch edu params: {e}")
        return "autoplay=1&rel=0&modestbranding=1"

def safe_request(url, timeout=(2, 5)):
    try:
        res = http_session.get(url, headers=get_random_headers(), timeout=timeout)
        res.raise_for_status()
        return res.json()
    except:
        return None

def request_invidious_api(path, timeout=(2, 5)):
    random_instances = random.sample(INVIDIOUS_INSTANCES, min(3, len(INVIDIOUS_INSTANCES)))
    for instance in random_instances:
        try:
            url = instance + 'api/v1' + path
            res = http_session.get(url, headers=get_random_headers(), timeout=timeout)
            if res.status_code == 200:
                return res.json()
        except:
            continue
    return None

def get_youtube_search(query, max_results=20):
    if YOUTUBE_API_KEY:
        url = f"https://www.googleapis.com/youtube/v3/search?part=snippet&type=video&q={urllib.parse.quote(query)}&maxResults={max_results}&key={YOUTUBE_API_KEY}"
        try:
            res = http_session.get(url, timeout=5)
            res.raise_for_status()
            data = res.json()
            results = []
            for item in data.get('items', []):
                snippet = item.get('snippet', {})
                results.append({
                    'type': 'video',
                    'id': item.get('id', {}).get('videoId', ''),
                    'title': snippet.get('title', ''),
                    'author': snippet.get('channelTitle', ''),
                    'authorId': snippet.get('channelId', ''),
                    'thumbnail': f"https://i.ytimg.com/vi/{item.get('id', {}).get('videoId', '')}/hqdefault.jpg",
                    'published': snippet.get('publishedAt', ''),
                    'description': snippet.get('description', ''),
                    'views': '',
                    'length': ''
                })
            return results
        except Exception as e:
            print(f"YouTube API error: {e}")

    return invidious_search(query)

def invidious_search(query, page=1):
    path = f"/search?q={urllib.parse.quote(query)}&page={page}&hl=jp"
    data = request_invidious_api(path)

    if not data:
        return []

    results = []
    for item in data:
        item_type = item.get('type', '')

        if item_type == 'video':
            length_seconds = item.get('lengthSeconds', 0)
            results.append({
                'type': 'video',
                'id': item.get('videoId', ''),
                'title': item.get('title', ''),
                'author': item.get('author', ''),
                'authorId': item.get('authorId', ''),
                'thumbnail': f"https://i.ytimg.com/vi/{item.get('videoId', '')}/hqdefault.jpg",
                'published': item.get('publishedText', ''),
                'views': item.get('viewCountText', ''),
                'length': str(datetime.timedelta(seconds=length_seconds)) if length_seconds else ''
            })
        elif item_type == 'channel':
            thumbnails = item.get('authorThumbnails', [])
            thumb_url = thumbnails[-1].get('url', '') if thumbnails else ''
            if thumb_url and not thumb_url.startswith('https'):
                thumb_url = 'https:' + thumb_url
            results.append({
                'type': 'channel',
                'id': item.get('authorId', ''),
                'author': item.get('author', ''),
                'thumbnail': thumb_url,
                'subscribers': item.get('subCount', 0)
            })
        elif item_type == 'playlist':
            results.append({
                'type': 'playlist',
                'id': item.get('playlistId', ''),
                'title': item.get('title', ''),
                'thumbnail': item.get('playlistThumbnail', ''),
                'count': item.get('videoCount', 0)
            })

    return results

def get_video_info(video_id):
    path = f"/videos/{urllib.parse.quote(video_id)}"
    data = request_invidious_api(path, timeout=(5, 15))

    if not data:
        try:
            res = http_session.get(f"{EDU_VIDEO_API}{video_id}", headers=get_random_headers(), timeout=(2, 6))
            res.raise_for_status()
            edu_data = res.json()

            related_videos = []
            for item in edu_data.get('related', [])[:20]:
                related_videos.append({
                    'id': item.get('videoId', ''),
                    'title': item.get('title', ''),
                    'author': item.get('channel', ''),
                    'authorId': item.get('channelId', ''),
                    'views': item.get('views', ''),
                    'thumbnail': f"https://i.ytimg.com/vi/{item.get('videoId', '')}/mqdefault.jpg",
                    'length': ''
                })

            return {
                'title': edu_data.get('title', ''),
                'description': edu_data.get('description', {}).get('formatted', ''),
                'author': edu_data.get('author', {}).get('name', ''),
                'authorId': edu_data.get('author', {}).get('id', ''),
                'authorThumbnail': edu_data.get('author', {}).get('thumbnail', ''),
                'views': edu_data.get('views', ''),
                'likes': edu_data.get('likes', ''),
                'subscribers': edu_data.get('author', {}).get('subscribers', ''),
                'published': edu_data.get('relativeDate', ''),
                'related': related_videos,
                'streamUrls': [],
                'highstreamUrl': None,
                'audioUrl': None
            }
        except Exception as e:
            print(f"EDU Video API error: {e}")
            return None

    recommended = data.get('recommendedVideos', data.get('recommendedvideo', []))
    related_videos = []
    for item in recommended[:20]:
        length_seconds = item.get('lengthSeconds', 0)
        related_videos.append({
            'id': item.get('videoId', ''),
            'title': item.get('title', ''),
            'author': item.get('author', ''),
            'authorId': item.get('authorId', ''),
            'views': item.get('viewCountText', ''),
            'thumbnail': f"https://i.ytimg.com/vi/{item.get('videoId', '')}/mqdefault.jpg",
            'length': str(datetime.timedelta(seconds=length_seconds)) if length_seconds else ''
        })

    adaptive_formats = data.get('adaptiveFormats', [])
    stream_urls = []
    highstream_url = None
    audio_url = None

    for stream in adaptive_formats:
        if stream.get('container') == 'webm' and stream.get('resolution'):
            stream_urls.append({
                'url': stream.get('url', ''),
                'resolution': stream.get('resolution', '')
            })
            if stream.get('resolution') == '1080p' and not highstream_url:
                highstream_url = stream.get('url')
            elif stream.get('resolution') == '720p' and not highstream_url:
                highstream_url = stream.get('url')

    for stream in adaptive_formats:
        if stream.get('container') == 'm4a' and stream.get('audioQuality') == 'AUDIO_QUALITY_MEDIUM':
            audio_url = stream.get('url')
            break

    format_streams = data.get('formatStreams', [])
    video_urls = [stream.get('url', '') for stream in reversed(format_streams)][:2]

    author_thumbnails = data.get('authorThumbnails', [])
    author_thumbnail = author_thumbnails[-1].get('url', '') if author_thumbnails else ''

    return {
        'title': data.get('title', ''),
        'description': data.get('descriptionHtml', '').replace('\n', '<br>'),
        'author': data.get('author', ''),
        'authorId': data.get('authorId', ''),
        'authorThumbnail': author_thumbnail,
        'views': data.get('viewCount', 0),
        'likes': data.get('likeCount', 0),
        'subscribers': data.get('subCountText', ''),
        'published': data.get('publishedText', ''),
        'lengthText': str(datetime.timedelta(seconds=data.get('lengthSeconds', 0))),
        'related': related_videos,
        'videoUrls': video_urls,
        'streamUrls': stream_urls,
        'highstreamUrl': highstream_url,
        'audioUrl': audio_url
    }

def get_playlist_info(playlist_id):
    path = f"/playlists/{urllib.parse.quote(playlist_id)}"
    data = request_invidious_api(path, timeout=(5, 15))

    if not data:
        return None

    videos = []
    for item in data.get('videos', []):
        length_seconds = item.get('lengthSeconds', 0)
        videos.append({
            'type': 'video',
            'id': item.get('videoId', ''),
            'title': item.get('title', ''),
            'author': item.get('author', ''),
            'authorId': item.get('authorId', ''),
            'thumbnail': f"https://i.ytimg.com/vi/{item.get('videoId', '')}/hqdefault.jpg",
            'length': str(datetime.timedelta(seconds=length_seconds)) if length_seconds else ''
        })

    return {
        'id': playlist_id,
        'title': data.get('title', ''),
        'author': data.get('author', ''),
        'authorId': data.get('authorId', ''),
        'description': data.get('description', ''),
        'videoCount': data.get('videoCount', 0),
        'viewCount': data.get('viewCount', 0),
        'videos': videos
    }

def get_channel_info(channel_id):
    path = f"/channels/{urllib.parse.quote(channel_id)}"
    data = request_invidious_api(path, timeout=(5, 15))

    if not data:
        return None

    latest_videos = data.get('latestVideos', data.get('latestvideo', []))
    videos = []
    for item in latest_videos:
        length_seconds = item.get('lengthSeconds', 0)
        videos.append({
            'type': 'video',
            'id': item.get('videoId', ''),
            'title': item.get('title', ''),
            'author': data.get('author', ''),
            'authorId': data.get('authorId', ''),
            'published': item.get('publishedText', ''),
            'views': item.get('viewCountText', ''),
            'length': str(datetime.timedelta(seconds=length_seconds)) if length_seconds else ''
        })

    author_thumbnails = data.get('authorThumbnails', [])
    author_thumbnail = author_thumbnails[-1].get('url', '') if author_thumbnails else ''

    author_banners = data.get('authorBanners', [])
    author_banner = urllib.parse.quote(author_banners[0].get('url', ''), safe='-_.~/:'
    ) if author_banners else ''

    return {
        'videos': videos,
        'channelName': data.get('author', ''),
        'channelIcon': author_thumbnail,
        'channelProfile': data.get('descriptionHtml', ''),
        'authorBanner': author_banner,
        'subscribers': data.get('subCount', 0),
        'tags': data.get('tags', []),
        'videoCount': data.get('videoCount', 0)
    }

def get_channel_videos(channel_id, continuation=None):
    path = f"/channels/{urllib.parse.quote(channel_id)}/videos"
    if continuation:
        path += f"?continuation={urllib.parse.quote(continuation)}"

    data = request_invidious_api(path, timeout=(5, 15))

    if not data:
        return None

    videos = []
    for item in data.get('videos', []):
        length_seconds = item.get('lengthSeconds', 0)
        videos.append({
            'type': 'video',
            'id': item.get('videoId', ''),
            'title': item.get('title', ''),
            'author': item.get('author', ''),
            'authorId': item.get('authorId', ''),
            'published': item.get('publishedText', ''),
            'views': item.get('viewCountText', ''),
            'length': str(datetime.timedelta(seconds=length_seconds)) if length_seconds else ''
        })

    return {
        'videos': videos,
        'continuation': data.get('continuation', '')
    }

def get_stream_url(video_id):
    edu_params = get_edu_params()
    urls = {
        'primary': None,
        'fallback': None,
        'm3u8': None,
        'embed': f"https://www.youtube-nocookie.com/embed/{video_id}?autoplay=1",
        'education': f"https://www.youtubeeducation.com/embed/{video_id}?{edu_params}"
    }

    try:
        res = http_session.get(f"{STREAM_API}{video_id}", headers=get_random_headers(), timeout=(3, 6))
        if res.status_code == 200:
            data = res.json()
            formats = data.get('formats', [])

            for fmt in formats:
                if fmt.get('itag') == '18':
                    urls['primary'] = fmt.get('url')
                    break

            if not urls['primary']:
                for fmt in formats:
                    if fmt.get('url') and fmt.get('vcodec') != 'none':
                        urls['fallback'] = fmt.get('url')
                        break
    except:
        pass

    try:
        res = http_session.get(f"{M3U8_API}{video_id}", headers=get_random_headers(), timeout=(3, 6))
        if res.status_code == 200:
            data = res.json()
            m3u8_formats = data.get('m3u8_formats', [])
            if m3u8_formats:
                best = max(m3u8_formats, key=lambda x: int(x.get('resolution', '0x0').split('x')[-1] or 0))
                urls['m3u8'] = best.get('url')
    except:
        pass

    return urls

def get_comments(video_id):
    path = f"/comments/{urllib.parse.quote(video_id)}?hl=jp"
    data = request_invidious_api(path)

    if not data:
        return []

    comments = []
    for item in data.get('comments', []):
        thumbnails = item.get('authorThumbnails', [])
        author_thumbnail = thumbnails[-1].get('url', '') if thumbnails else ''
        comments.append({
            'author': item.get('author', ''),
            'authorThumbnail': author_thumbnail,
            'authorId': item.get('authorId', ''),
            'content': item.get('contentHtml', '').replace('\n', '<br>'),
            'likes': item.get('likeCount', 0),
            'published': item.get('publishedText', '')
        })

    return comments

def get_trending():
    cache_duration = 300
    current_time = time.time()

    if _trending_cache['data'] and (current_time - _trending_cache['timestamp']) < cache_duration:
        return _trending_cache['data']

    path = "/popular"
    data = request_invidious_api(path, timeout=(2, 4))

    if data:
        results = []
        for item in data[:24]:
            if item.get('type') in ['video', 'shortVideo']:
                results.append({
                    'type': 'video',
                    'id': item.get('videoId', ''),
                    'title': item.get('title', ''),
                    'author': item.get('author', ''),
                    'thumbnail': f"https://i.ytimg.com/vi/{item.get('videoId', '')}/hqdefault.jpg",
                    'published': item.get('publishedText', ''),
                    'views': item.get('viewCountText', '')
                })
        if results:
            _trending_cache['data'] = results
            _trending_cache['timestamp'] = current_time
            return results

    default_videos = [
        {'type': 'video', 'id': 'dQw4w9WgXcQ', 'title': 'Rick Astley - Never Gonna Give You Up', 'author': 'Rick Astley', 'thumbnail': 'https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg', 'published': '', 'views': '17億 回視聴'},
        {'type': 'video', 'id': 'kJQP7kiw5Fk', 'title': 'Luis Fonsi - Despacito ft. Daddy Yankee', 'author': 'Luis Fonsi', 'thumbnail': 'https://i.ytimg.com/vi/kJQP7kiw5Fk/hqdefault.jpg', 'published': '', 'views': '80億 回視聴'},
        {'type': 'video', 'id': 'JGwWNGJdvx8', 'title': 'Ed Sheeran - Shape of You', 'author': 'Ed Sheeran', 'thumbnail': 'https://i.ytimg.com/vi/JGwWNGJdvx8/hqdefault.jpg', 'published': '', 'views': '64億 回視聴'},
        {'type': 'video', 'id': 'RgKAFK5djSk', 'title': 'Wiz Khalifa - See You Again ft. Charlie Puth', 'author': 'Wiz Khalifa', 'thumbnail': 'https://i.ytimg.com/vi/RgKAFK5djSk/hqdefault.jpg', 'published': '', 'views': '60億 回視聴'},
        {'type': 'video', 'id': 'OPf0YbXqDm0', 'title': 'Mark Ronson - Uptown Funk ft. Bruno Mars', 'author': 'Mark Ronson', 'thumbnail': 'https://i.ytimg.com/vi/OPf0YbXqDm0/hqdefault.jpg', 'published': '', 'views': '50億 回視聴'},
        {'type': 'video', 'id': '9bZkp7q19f0', 'title': 'PSY - Gangnam Style', 'author': 'PSY', 'thumbnail': 'https://i.ytimg.com/vi/9bZkp7q19f0/hqdefault.jpg', 'published': '', 'views': '50億 回視聴'},
        {'type': 'video', 'id': 'XqZsoesa55w', 'title': 'Baby Shark Dance', 'author': 'Pinkfong', 'thumbnail': 'https://i.ytimg.com/vi/XqZsoesa55w/hqdefault.jpg', 'published': '', 'views': '150億 回視聴'},
        {'type': 'video', 'id': 'fJ9rUzIMcZQ', 'title': 'Queen - Bohemian Rhapsody', 'author': 'Queen Official', 'thumbnail': 'https://i.ytimg.com/vi/fJ9rUzIMcZQ/hqdefault.jpg', 'published': '', 'views': '16億 回視聴'},
    ]
    return default_videos

def get_suggestions(keyword):
    try:
        url = f"https://suggestqueries.google.com/complete/search?client=firefox&ds=yt&q={urllib.parse.quote(keyword)}"
        res = http_session.get(url, headers=get_random_headers(), timeout=2)
        if res.status_code == 200:
            data = res.json()
            return data[1] if len(data) > 1 else []
    except:
        pass
    return []

@app.route('/login', methods=['GET', 'POST'])
def login():
    if session.get('logged_in'):
        return redirect(url_for('index'))

    error = None
    if request.method == 'POST':
        password = request.form.get('password', '')
        if password == PASSWORD:
            session['logged_in'] = True
            return redirect(url_for('index'))
        else:
            error = 'パスワードが間違っています'

    return render_template('login.html', error=error)

@app.route('/')
@login_required
def index():
    theme = request.cookies.get('theme', 'dark')
    return render_template('home.html', theme=theme)

@app.route('/trend')
@login_required
def trend():
    theme = request.cookies.get('theme', 'dark')
    trending = get_trending()
    return render_template('index.html', videos=trending, theme=theme)

@app.route('/search')
@login_required
def search():
    query = request.args.get('q', '')
    page = request.args.get('page', '1')
    vc = request.cookies.get('vc', '1')
    proxy = request.cookies.get('proxy', 'False')
    theme = request.cookies.get('theme', 'dark')

    if not query:
        return render_template('search.html', results=[], query='', vc=vc, proxy=proxy, theme=theme, next='')

    results = get_youtube_search(query) if page == '1' else invidious_search(query, int(page))
    next_page = f"/search?q={urllib.parse.quote(query)}&page={int(page) + 1}"

    return render_template('search.html', results=results, query=query, vc=vc, proxy=proxy, theme=theme, next=next_page)

@app.route('/watch')
@login_required
def watch():
    video_id = request.args.get('v', '')
    playlist_id = request.args.get('list', '')
    playlist_index = request.args.get('index', '0')
    theme = request.cookies.get('theme', 'dark')
    proxy = request.cookies.get('proxy', 'False')

    if not video_id:
        return render_template('index.html', videos=get_trending(), theme=theme)

    video_info = get_video_info(video_id)
    stream_urls = get_stream_url(video_id)
    comments = get_comments(video_id)

    playlist_videos = []
    playlist_title = ''
    if playlist_id:
        playlist_info = get_playlist_info(playlist_id)
        if playlist_info:
            playlist_videos = playlist_info.get('videos', [])
            playlist_title = playlist_info.get('title', '')

    return render_template('watch.html',
                         video_id=video_id,
                         video=video_info,
                         streams=stream_urls,
                         comments=comments,
                         mode='stream',
                         theme=theme,
                         proxy=proxy,
                         playlist_id=playlist_id,
                         playlist_index=int(playlist_index),
                         playlist_videos=playlist_videos,
                         playlist_title=playlist_title)

@app.route('/w')
@login_required
def watch_high_quality():
    video_id = request.args.get('v', '')
    playlist_id = request.args.get('list', '')
    playlist_index = request.args.get('index', '0')
    theme = request.cookies.get('theme', 'dark')
    proxy = request.cookies.get('proxy', 'False')

    if not video_id:
        return render_template('index.html', videos=get_trending(), theme=theme)

    video_info = get_video_info(video_id)
    stream_urls = get_stream_url(video_id)
    comments = get_comments(video_id)

    playlist_videos = []
    playlist_title = ''
    if playlist_id:
        playlist_info = get_playlist_info(playlist_id)
        if playlist_info:
            playlist_videos = playlist_info.get('videos', [])
            playlist_title = playlist_info.get('title', '')

    return render_template('watch.html',
                         video_id=video_id,
                         video=video_info,
                         streams=stream_urls,
                         comments=comments,
                         mode='high',
                         theme=theme,
                         proxy=proxy,
                         playlist_id=playlist_id,
                         playlist_index=int(playlist_index),
                         playlist_videos=playlist_videos,
                         playlist_title=playlist_title)

@app.route('/ume')
@login_required
def watch_embed():
    video_id = request.args.get('v', '')
    playlist_id = request.args.get('list', '')
    playlist_index = request.args.get('index', '0')
    theme = request.cookies.get('theme', 'dark')
    proxy = request.cookies.get('proxy', 'False')

    if not video_id:
        return render_template('index.html', videos=get_trending(), theme=theme)

    video_info = get_video_info(video_id)
    stream_urls = get_stream_url(video_id)
    comments = get_comments(video_id)

    playlist_videos = []
    playlist_title = ''
    if playlist_id:
        playlist_info = get_playlist_info(playlist_id)
        if playlist_info:
            playlist_videos = playlist_info.get('videos', [])
            playlist_title = playlist_info.get('title', '')

    return render_template('watch.html',
                         video_id=video_id,
                         video=video_info,
                         streams=stream_urls,
                         comments=comments,
                         mode='embed',
                         theme=theme,
                         proxy=proxy,
                         playlist_id=playlist_id,
                         playlist_index=int(playlist_index),
                         playlist_videos=playlist_videos,
                         playlist_title=playlist_title)

@app.route('/edu')
@login_required
def watch_education():
    video_id = request.args.get('v', '')
    playlist_id = request.args.get('list', '')
    playlist_index = request.args.get('index', '0')
    theme = request.cookies.get('theme', 'dark')
    proxy = request.cookies.get('proxy', 'False')

    if not video_id:
        return render_template('index.html', videos=get_trending(), theme=theme)

    video_info = get_video_info(video_id)
    stream_urls = get_stream_url(video_id)
    comments = get_comments(video_id)

    playlist_videos = []
    playlist_title = ''
    if playlist_id:
        playlist_info = get_playlist_info(playlist_id)
        if playlist_info:
            playlist_videos = playlist_info.get('videos', [])
            playlist_title = playlist_info.get('title', '')

    return render_template('watch.html',
                         video_id=video_id,
                         video=video_info,
                         streams=stream_urls,
                         comments=comments,
                         mode='education',
                         theme=theme,
                         proxy=proxy,
                         playlist_id=playlist_id,
                         playlist_index=int(playlist_index),
                         playlist_videos=playlist_videos,
                         playlist_title=playlist_title)

@app.route('/channel/<channel_id>')
@login_required
def channel(channel_id):
    theme = request.cookies.get('theme', 'dark')
    vc = request.cookies.get('vc', '1')
    proxy = request.cookies.get('proxy', 'False')

    channel_info = get_channel_info(channel_id)

    if not channel_info:
        return render_template('channel.html', channel=None, videos=[], theme=theme, vc=vc, proxy=proxy, channel_id=channel_id, continuation='', total_videos=0)

    channel_videos = get_channel_videos(channel_id)
    videos = channel_videos.get('videos', []) if channel_videos else channel_info.get('videos', [])
    continuation = channel_videos.get('continuation', '') if channel_videos else ''
    total_videos = channel_info.get('videoCount', 0)

    return render_template('channel.html',
                         channel=channel_info,
                         videos=videos,
                         theme=theme,
                         vc=vc,
                         proxy=proxy,
                         channel_id=channel_id,
                         continuation=continuation,
                         total_videos=total_videos)

@app.route('/tool')
@login_required
def tool_page():
    theme = request.cookies.get('theme', 'dark')
    return render_template('tool.html', theme=theme)

@app.route('/history')
@login_required
def history_page():
    theme = request.cookies.get('theme', 'dark')
    return render_template('history.html', theme=theme)

@app.route('/favorite')
@login_required
def favorite_page():
    theme = request.cookies.get('theme', 'dark')
    return render_template('favorite.html', theme=theme)

@app.route('/help')
@login_required
def help_page():
    theme = request.cookies.get('theme', 'dark')
    return render_template('help.html', theme=theme)

@app.route('/blog')
@login_required
def blog_page():
    theme = request.cookies.get('theme', 'dark')
    posts = [
        {
            'date': '2025-11-30',
            'category': 'お知らせ',
            'title': 'チョコTubeへようこそ！',
            'excerpt': 'youtubeサイトを作ってみたよ～',
            'content': '<p>読み込みが遅いだって？しゃーない。これから改善させるよ</p><p>あとはbbs(チャット)とかゲームとか追加したいなぁ<br>ちなみに何か意見とか聞きたいこととかあったら<a href="https://scratch.mit.edu/projects/1249572814/">ここでコメント</a>してね。</p>'
        }
    ]
    return render_template('blog.html', theme=theme, posts=posts)

@app.route('/chat')
@login_required
def chat_page():
    theme = request.cookies.get('theme', 'dark')
    chat_server_url = os.environ.get('CHAT_SERVER_URL', '')
    return render_template('chat.html', theme=theme, chat_server_url=chat_server_url)

@app.route('/downloader')
@login_required
def downloader_page():
    theme = request.cookies.get('theme', 'dark')
    return render_template('downloader.html', theme=theme)

@app.route('/api/video-info/<video_id>')
@login_required
def api_video_info(video_id):
    info = get_video_info(video_id)
    if not info:
        return jsonify({'error': '動画情報を取得できませんでした'}), 404
    return jsonify(info)

@app.route('/api/download/<video_id>')
@login_required
def api_download(video_id):
    format_type = request.args.get('format', 'video')
    quality = request.args.get('quality', '720')

    if format_type == 'audio':
        download_url = f"https://api.cobalt.tools/api/json"
        try:
            payload = {
                "url": f"https://www.youtube.com/watch?v={video_id}",
                "vCodec": "h264",
                "vQuality": "720",
                "aFormat": "mp3",
                "isAudioOnly": True
            }
            headers = {
                "Accept": "application/json",
                "Content-Type": "application/json"
            }
            res = http_session.post(download_url, json=payload, headers=headers, timeout=10)
            if res.status_code == 200:
                data = res.json()
                if data.get('url'):
                    return redirect(data['url'])
        except Exception as e:
            print(f"Cobalt API error: {e}")

        fallback_url = f"https://dl.y2mate.is/mates/convert?id={video_id}&format=mp3&quality=128"
        return redirect(fallback_url)
    else:
        download_url = f"https://api.cobalt.tools/api/json"
        try:
            payload = {
                "url": f"https://www.youtube.com/watch?v={video_id}",
                "vCodec": "h264",
                "vQuality": quality,
                "aFormat": "mp3",
                "isAudioOnly": False
            }
            headers = {
                "Accept": "application/json",
                "Content-Type": "application/json"
            }
            res = http_session.post(download_url, json=payload, headers=headers, timeout=10)
            if res.status_code == 200:
                data = res.json()
                if data.get('url'):
                    return redirect(data['url'])
        except Exception as e:
            print(f"Cobalt API error: {e}")

        fallback_url = f"https://dl.y2mate.is/mates/convert?id={video_id}&format=mp4&quality={quality}"
        return redirect(fallback_url)

DOWNLOAD_DIR = tempfile.gettempdir()

def sanitize_filename(filename):
    filename = re.sub(r'[<>:"/\\|?*]', '', filename)
    filename = filename.strip()
    if len(filename) > 100:
        filename = filename[:100]
    return filename

def cleanup_old_downloads():
    try:
        current_time = time.time()
        for f in os.listdir(DOWNLOAD_DIR):
            if f.startswith('chocotube_') and (f.endswith('.mp4') or f.endswith('.mp3')):
                filepath = os.path.join(DOWNLOAD_DIR, f)
                if os.path.isfile(filepath):
                    file_age = current_time - os.path.getmtime(filepath)
                    if file_age > 600:
                        os.remove(filepath)
    except Exception as e:
        print(f"Cleanup error: {e}")

@app.route('/api/internal-download/<video_id>')
@login_required
def api_internal_download(video_id):
    format_type = request.args.get('format', 'mp4')
    quality = request.args.get('quality', '720')

    video_url = f"https://www.youtube.com/watch?v={video_id}"

    cleanup_old_downloads()

    unique_id = f"{video_id}_{int(time.time())}"
    cookie_file = os.path.join(DOWNLOAD_DIR, f'cookies_{unique_id}.txt')

    try:
        cookies_content = """# Netscape HTTP Cookie File
.youtube.com    TRUE    /       TRUE    2147483647      CONSENT YES+cb
.youtube.com    TRUE    /       TRUE    2147483647      PREF    hl=ja&gl=JP
"""
        with open(cookie_file, 'w') as f:
            f.write(cookies_content)

        base_opts = {
            'quiet': True,
            'no_warnings': True,
            'cookiefile': cookie_file,
            'http_headers': {
                'User-Agent': random.choice(USER_AGENTS),
                'Accept-Language': 'ja-JP,ja;q=0.9,en;q=0.8',
            },
            'socket_timeout': 30,
            'retries': 3,
        }

        if format_type == 'mp3':
            output_path = os.path.join(DOWNLOAD_DIR, f'chocotube_{unique_id}.mp3')
            ydl_opts = {
                **base_opts,
                'format': 'bestaudio[ext=m4a]/bestaudio/best',
                'outtmpl': os.path.join(DOWNLOAD_DIR, f'chocotube_{unique_id}.%(ext)s'),
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192',
                }],
            }
        else:
            output_path = os.path.join(DOWNLOAD_DIR, f'chocotube_{unique_id}.mp4')
            format_string = f'bestvideo[height<={quality}][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<={quality}]+bestaudio/best[height<={quality}]/best'
            ydl_opts = {
                **base_opts,
                'format': format_string,
                'outtmpl': os.path.join(DOWNLOAD_DIR, f'chocotube_{unique_id}.%(ext)s'),
                'merge_output_format': 'mp4',
            }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=True)
            title = sanitize_filename(info.get('title', video_id) if info else video_id)

        if os.path.exists(cookie_file):
            os.remove(cookie_file)

        if format_type == 'mp3':
            if os.path.exists(output_path):
                return send_file(
                    output_path,
                    as_attachment=True,
                    download_name=f"{title}.mp3",
                    mimetype='audio/mpeg'
                )
            for ext in ['mp3', 'm4a', 'webm', 'opus']:
                check_path = os.path.join(DOWNLOAD_DIR, f'chocotube_{unique_id}.{ext}')
                if os.path.exists(check_path):
                    return send_file(
                        check_path,
                        as_attachment=True,
                        download_name=f"{title}.mp3",
                        mimetype='audio/mpeg'
                    )
        else:
            if os.path.exists(output_path):
                return send_file(
                    output_path,
                    as_attachment=True,
                    download_name=f"{title}.mp4",
                    mimetype='video/mp4'
                )
            for ext in ['mp4', 'mkv', 'webm']:
                check_path = os.path.join(DOWNLOAD_DIR, f'chocotube_{unique_id}.{ext}')
                if os.path.exists(check_path):
                    return send_file(
                        check_path,
                        as_attachment=True,
                        download_name=f"{title}.mp4",
                        mimetype='video/mp4'
                    )

        return jsonify({
            'success': False,
            'error': 'ファイルのダウンロードに失敗しました'
        }), 500

    except Exception as e:
        print(f"Internal download error: {e}")
        if os.path.exists(cookie_file):
            try:
                os.remove(cookie_file)
            except:
                pass
        return jsonify({
            'success': False,
            'error': f'ダウンロードエラー: {str(e)}'
        }), 500

@app.route('/api/stream/<video_id>')
@login_required
def api_stream(video_id):
    try:
        stream_url = f"https://siawaseok.duckdns.org/api/stream/{video_id}/type2"
        res = http_session.get(stream_url, headers=get_random_headers(), timeout=15)
        if res.status_code == 200:
            data = res.json()
            return jsonify(data)
        else:
            return jsonify({'error': 'ストリームデータの取得に失敗しました'}), res.status_code
    except Exception as e:
        print(f"Stream API error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/lite-download/<video_id>')
@login_required
def api_lite_download(video_id):
    format_type = request.args.get('format', 'mp4')
    quality = request.args.get('quality', '360')

    try:
        stream_url = f"https://siawaseok.duckdns.org/api/stream/{video_id}/type2"
        res = http_session.get(stream_url, headers=get_random_headers(), timeout=15)

        if res.status_code != 200:
            return jsonify({'error': 'ストリームデータの取得に失敗しました', 'success': False}), 500

        data = res.json()
        videourl = data.get('videourl', {})

        if format_type == 'mp3' or format_type == 'm4a':
            audio_url = None
            for q in ['144p', '240p', '360p', '480p', '720p']:
                if q in videourl and videourl[q].get('audio', {}).get('url'):
                    audio_url = videourl[q]['audio']['url']
                    break

            if audio_url:
                return jsonify({
                    'success': True,
                    'url': audio_url,
                    'format': 'm4a',
                    'quality': 'audio',
                    'actual_format': 'm4a'
                })
            else:
                return jsonify({'error': '音声URLが見つかりませんでした', 'success': False}), 404
        elif format_type == 'mp4':
            quality_order = [quality + 'p', '360p', '480p', '720p', '240p', '144p']
            video_url = None
            actual_quality = None

            for q in quality_order:
                if q in videourl and videourl[q].get('video', {}).get('url'):
                    video_url = videourl[q]['video']['url']
                    actual_quality = q
                    break

            if video_url:
                return jsonify({
                    'success': True,
                    'url': video_url,
                    'format': 'mp4',
                    'quality': actual_quality,
                    'actual_format': 'mp4'
                })
            else:
                return jsonify({'error': '動画URLが見つかりませんでした', 'success': False}), 404
        else:
            return jsonify({'error': '無効なフォーマットです', 'success': False}), 400

    except Exception as e:
        print(f"Lite download error: {e}")
        return jsonify({'error': str(e), 'success': False}), 500

@app.route('/api/audio-stream/<video_id>')
@login_required
def api_audio_stream(video_id):
    try:
        ydl_opts = {
            'format': 'bestaudio[ext=m4a]/bestaudio/best',
            'quiet': True,
            'no_warnings': True,
            'extract_flat': False,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)

            audio_url = info.get('url')

            if not audio_url:
                formats = info.get('formats', [])
                for fmt in formats:
                    if fmt.get('acodec') != 'none' and fmt.get('vcodec') == 'none':
                        audio_url = fmt.get('url')
                        if audio_url and 'googlevideo.com' in audio_url:
                            break

                if not audio_url:
                    for fmt in formats:
                        if fmt.get('acodec') != 'none':
                            url = fmt.get('url', '')
                            if 'googlevideo.com' in url:
                                audio_url = url
                                break

            if audio_url and 'googlevideo.com' in audio_url:
                return jsonify({
                    'success': True,
                    'url': audio_url,
                    'title': info.get('title', ''),
                    'format': 'audio',
                    'source': 'googlevideo'
                })
            elif audio_url:
                return jsonify({
                    'success': True,
                    'url': audio_url,
                    'title': info.get('title', ''),
                    'format': 'audio',
                    'source': 'other'
                })
            else:
                return jsonify({'success': False, 'error': '音声URLが見つかりませんでした'}), 404

    except Exception as e:
        print(f"Audio stream error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/thumbnail-download/<video_id>')
@login_required
def api_thumbnail_download(video_id):
    quality = request.args.get('quality', 'hq')

    quality_map = {
        'max': 'maxresdefault',
        'sd': 'sddefault',
        'hq': 'hqdefault',
        'mq': 'mqdefault',
        'default': 'default'
    }

    thumbnail_name = quality_map.get(quality, 'hqdefault')
    thumbnail_url = f"https://i.ytimg.com/vi/{video_id}/{thumbnail_name}.jpg"

    try:
        res = http_session.get(thumbnail_url, headers=get_random_headers(), timeout=10)

        if res.status_code == 200 and len(res.content) > 1000:
            response = Response(res.content, mimetype='image/jpeg')
            response.headers['Content-Disposition'] = f'attachment; filename="{video_id}_{thumbnail_name}.jpg"'
            return response

        if quality != 'hq':
            fallback_url = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
            res = http_session.get(fallback_url, headers=get_random_headers(), timeout=10)
            if res.status_code == 200:
                response = Response(res.content, mimetype='image/jpeg')
                response.headers['Content-Disposition'] = f'attachment; filename="{video_id}_hqdefault.jpg"'
                return response

        return jsonify({'error': 'サムネイルの取得に失敗しました', 'success': False}), 404

    except Exception as e:
        print(f"Thumbnail download error: {e}")
        return jsonify({'error': str(e), 'success': False}), 500

@app.route('/playlist')
@login_required
def playlist_page():
    playlist_id = request.args.get('list', '')
    theme = request.cookies.get('theme', 'dark')
    vc = request.cookies.get('vc', '1')

    if not playlist_id:
        return redirect(url_for('index'))

    playlist_info = get_playlist_info(playlist_id)

    if not playlist_info:
        return render_template('playlist.html', playlist=None, videos=[], theme=theme, vc=vc)

    return render_template('playlist.html',
                         playlist=playlist_info,
                         videos=playlist_info.get('videos', []),
                         theme=theme,
                         vc=vc)

@app.route('/thumbnail')
def thumbnail():
    video_id = request.args.get('v', '')
    if not video_id:
        return '', 404

    current_time = time.time()
    cache_key = video_id
    if cache_key in _thumbnail_cache:
        cached_data, cached_time = _thumbnail_cache[cache_key]
        if current_time - cached_time < 3600:
            response = Response(cached_data, mimetype='image/jpeg')
            response.headers['Cache-Control'] = 'public, max-age=3600'
            return response

    try:
        url = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
        res = http_session.get(url, headers=get_random_headers(), timeout=3)
        if len(_thumbnail_cache) > 500:
            oldest_key = min(_thumbnail_cache.keys(), key=lambda k: _thumbnail_cache[k][1])
            del _thumbnail_cache[oldest_key]
        _thumbnail_cache[cache_key] = (res.content, current_time)
        response = Response(res.content, mimetype='image/jpeg')
        response.headers['Cache-Control'] = 'public, max-age=3600'
        return response
    except:
        return '', 404

@app.route('/suggest')
def suggest():
    keyword = request.args.get('keyword', '')
    suggestions = get_suggestions(keyword)
    return jsonify(suggestions)

@app.route('/comments')
def comments_api():
    video_id = request.args.get('v', '')
    comments = get_comments(video_id)

    html = ''
    for comment in comments:
        html += f'''
        <div class="comment">
            <img src="{comment['authorThumbnail']}" alt="{comment['author']}" class="comment-avatar">
            <div class="comment-content">
                <div class="comment-header">
                    <a href="/channel/{comment['authorId']}" class="comment-author">{comment['author']}</a>
                    <span class="comment-date">{comment['published']}</span>
                </div>
                <div class="comment-text">{comment['content']}</div>
                <div class="comment-likes">👍 {comment['likes']}</div>
            </div>
        </div>
        '''

    return html if html else '<p class="no-comments">コメントはありません</p>'

@app.route('/api/search')
def api_search():
    query = request.args.get('q', '')
    if not query:
        return jsonify({'error': 'Query required'}), 400

    results = get_youtube_search(query)
    return jsonify(results)

@app.route('/api/video/<video_id>')
def api_video(video_id):
    info = get_video_info(video_id)
    streams = get_stream_url(video_id)
    return jsonify({'info': info, 'streams': streams})

@app.route('/api/trending')
def api_trending():
    videos = get_trending()
    return jsonify(videos)

@app.route('/api/channel/<channel_id>/videos')
def api_channel_videos(channel_id):
    continuation = request.args.get('continuation', '')
    result = get_channel_videos(channel_id, continuation if continuation else None)
    if not result:
        return jsonify({'videos': [], 'continuation': ''})
    return jsonify(result)

@app.route('/getcode')
@login_required
def getcode():
    theme = request.cookies.get('theme', 'dark')
    return render_template('getcode.html', theme=theme)

@app.route('/api/getcode')
@login_required
def api_getcode():
    url = request.args.get('url', '')

    if not url:
        return jsonify({'success': False, 'error': 'URLが必要です'})

    if not url.startswith('http://') and not url.startswith('https://'):
        return jsonify({'success': False, 'error': '有効なURLを入力してください'})

    try:
        headers = {
            'User-Agent': random.choice(USER_AGENTS),
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'ja,en-US;q=0.7,en;q=0.3',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive',
        }

        res = http_session.get(url, headers=headers, timeout=15, allow_redirects=True)
        res.raise_for_status()

        content_type = res.headers.get('Content-Type', '')
        if 'text/html' in content_type or 'text/plain' in content_type or 'application/xml' in content_type or 'text/xml' in content_type:
            try:
                code = res.text
            except:
                code = res.content.decode('utf-8', errors='replace')
        else:
            code = res.content.decode('utf-8', errors='replace')

        return jsonify({
            'success': True,
            'url': url,
            'code': code,
            'status_code': res.status_code,
            'content_type': content_type
        })

    except requests.exceptions.Timeout:
        return jsonify({'success': False, 'error': 'リクエストがタイムアウトしました'})
    except requests.exceptions.ConnectionError:
        return jsonify({'success': False, 'error': '接続エラーが発生しました'})
    except requests.exceptions.HTTPError as e:
        return jsonify({'success': False, 'error': f'HTTPエラー: {e.response.status_code}'})
    except Exception as e:
        return jsonify({'success': False, 'error': f'エラー: {str(e)}'})

CONVERTHUB_API_KEY = '155|hIxuoYFETaU54yeGE2zPWOw0NiSatCOhvJJYKy4Cb48c7d61'
TRANSLOADIT_API_KEY = 'R244EKuonluFkwhTYOu85vi6ZPm6mmZV'
TRANSLOADIT_SECRET = '4zVZ7eQm16qawPil8B4NJRr68kkCdMXQkd8NbNaq'
FREECONVERT_API_KEY = 'api_production_15cc009b9ac13759fb43f4946b3c950fee5e56e2f0214f242f6e9e4efc3093df.69393f3ea22aa85dd55c84ff.69393fa9142a194b36417393'
APIFY_API_TOKEN = 'apify_api_fpYkf6q1fqfJIz5S8bx4fcOeaP6CIM0iYpnu'

@app.route('/api/convert/converthub/<video_id>')
@login_required
def api_convert_converthub(video_id):
    """ConvertHub APIを使用してファイル形式を変換"""
    target_format = request.args.get('format', 'mp3')
    
    if not CONVERTHUB_API_KEY:
        return jsonify({'success': False, 'error': 'ConvertHub APIキーが設定されていません'}), 400
    
    try:
        video_url = f"https://www.youtube.com/watch?v={video_id}"
        unique_id = f"{video_id}_{int(time.time())}"
        
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'format': 'bestaudio[ext=m4a]/bestaudio/best',
            'outtmpl': os.path.join(DOWNLOAD_DIR, f'chocotube_convert_{unique_id}.%(ext)s'),
            'http_headers': {'User-Agent': random.choice(USER_AGENTS)},
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=True)
            title = sanitize_filename(info.get('title', video_id) if info else video_id)
        
        source_file = None
        for ext in ['m4a', 'webm', 'mp3', 'opus']:
            check_path = os.path.join(DOWNLOAD_DIR, f'chocotube_convert_{unique_id}.{ext}')
            if os.path.exists(check_path):
                source_file = check_path
                break
        
        if not source_file:
            return jsonify({'success': False, 'error': 'ダウンロードに失敗しました'}), 500
        
        headers = {
            'Authorization': f'Bearer {CONVERTHUB_API_KEY}'
        }
        
        with open(source_file, 'rb') as f:
            files = {'file': f}
            data = {'target_format': target_format}
            res = http_session.post(
                'https://api.converthub.com/v2/convert',
                files=files,
                data=data,
                headers=headers,
                timeout=120
            )
        
        if res.status_code == 200:
            job_data = res.json()
            job_id = job_data.get('job_id')
            
            for _ in range(60):
                time.sleep(2)
                status_res = http_session.get(
                    f'https://api.converthub.com/v2/jobs/{job_id}',
                    headers=headers,
                    timeout=30
                )
                if status_res.status_code == 200:
                    status = status_res.json()
                    if status.get('status') == 'completed':
                        download_url = status.get('result', {}).get('download_url')
                        if download_url:
                            if os.path.exists(source_file):
                                os.remove(source_file)
                            return jsonify({
                                'success': True,
                                'url': download_url,
                                'format': target_format,
                                'title': title,
                                'method': 'converthub'
                            })
                    elif status.get('status') == 'failed':
                        break
            
            if os.path.exists(source_file):
                os.remove(source_file)
            return jsonify({'success': False, 'error': '変換がタイムアウトしました'}), 500
        else:
            if os.path.exists(source_file):
                os.remove(source_file)
            return jsonify({'success': False, 'error': 'ConvertHub APIエラー'}), 500
            
    except Exception as e:
        print(f"ConvertHub convert error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/convert/transloadit/<video_id>')
@login_required
def api_convert_transloadit(video_id):
    """Transloadit APIを使用してファイル形式を変換"""
    target_format = request.args.get('format', 'mp3')
    bitrate = request.args.get('bitrate', '192000')
    
    if not TRANSLOADIT_API_KEY:
        return jsonify({'success': False, 'error': 'Transloadit APIキーが設定されていません'}), 400
    
    try:
        video_url = f"https://www.youtube.com/watch?v={video_id}"
        unique_id = f"{video_id}_{int(time.time())}"
        
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'format': 'bestaudio[ext=m4a]/bestaudio/best',
            'outtmpl': os.path.join(DOWNLOAD_DIR, f'chocotube_transloadit_{unique_id}.%(ext)s'),
            'http_headers': {'User-Agent': random.choice(USER_AGENTS)},
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=True)
            title = sanitize_filename(info.get('title', video_id) if info else video_id)
        
        source_file = None
        for ext in ['m4a', 'webm', 'mp3', 'opus']:
            check_path = os.path.join(DOWNLOAD_DIR, f'chocotube_transloadit_{unique_id}.{ext}')
            if os.path.exists(check_path):
                source_file = check_path
                break
        
        if not source_file:
            return jsonify({'success': False, 'error': 'ダウンロードに失敗しました'}), 500
        
        import hashlib
        import hmac
        
        expires = datetime.datetime.utcnow() + datetime.timedelta(hours=1)
        expires_str = expires.strftime('%Y/%m/%d %H:%M:%S+00:00')
        
        params = {
            'auth': {
                'key': TRANSLOADIT_API_KEY,
                'expires': expires_str
            },
            'steps': {
                ':original': {
                    'robot': '/upload/handle'
                },
                'encoded': {
                    'robot': '/audio/encode',
                    'use': ':original',
                    'preset': target_format,
                    'bitrate': int(bitrate),
                    'ffmpeg_stack': 'v6.0.0'
                }
            }
        }
        
        params_json = json.dumps(params)
        
        if TRANSLOADIT_SECRET:
            signature = hmac.new(
                TRANSLOADIT_SECRET.encode('utf-8'),
                params_json.encode('utf-8'),
                hashlib.sha384
            ).hexdigest()
        else:
            signature = ''
        
        with open(source_file, 'rb') as f:
            files = {'file': f}
            data = {'params': params_json}
            if signature:
                data['signature'] = f'sha384:{signature}'
            
            res = http_session.post(
                'https://api2.transloadit.com/assemblies',
                files=files,
                data=data,
                timeout=120
            )
        
        if res.status_code in [200, 201, 302]:
            assembly_data = res.json()
            assembly_url = assembly_data.get('assembly_ssl_url') or assembly_data.get('assembly_url')
            
            if assembly_url:
                for _ in range(60):
                    time.sleep(2)
                    status_res = http_session.get(assembly_url, timeout=30)
                    if status_res.status_code == 200:
                        status = status_res.json()
                        if status.get('ok') == 'ASSEMBLY_COMPLETED':
                            results = status.get('results', {})
                            encoded = results.get('encoded', [])
                            if encoded and len(encoded) > 0:
                                download_url = encoded[0].get('ssl_url') or encoded[0].get('url')
                                if download_url:
                                    if os.path.exists(source_file):
                                        os.remove(source_file)
                                    return jsonify({
                                        'success': True,
                                        'url': download_url,
                                        'format': target_format,
                                        'title': title,
                                        'method': 'transloadit'
                                    })
                        elif status.get('error'):
                            break
            
            if os.path.exists(source_file):
                os.remove(source_file)
            return jsonify({'success': False, 'error': '変換がタイムアウトしました'}), 500
        else:
            if os.path.exists(source_file):
                os.remove(source_file)
            return jsonify({'success': False, 'error': 'Transloadit APIエラー'}), 500
            
    except Exception as e:
        print(f"Transloadit convert error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/convert/freeconvert/<video_id>')
@login_required
def api_convert_freeconvert(video_id):
    """FreeConvert APIを使用してファイル形式を変換"""
    target_format = request.args.get('format', 'mp3')
    
    if not FREECONVERT_API_KEY:
        return jsonify({'success': False, 'error': 'FreeConvert APIキーが設定されていません'}), 400
    
    try:
        video_url = f"https://www.youtube.com/watch?v={video_id}"
        unique_id = f"{video_id}_{int(time.time())}"
        
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'format': 'bestaudio[ext=m4a]/bestaudio/best',
            'outtmpl': os.path.join(DOWNLOAD_DIR, f'chocotube_freeconvert_{unique_id}.%(ext)s'),
            'http_headers': {'User-Agent': random.choice(USER_AGENTS)},
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=True)
            title = sanitize_filename(info.get('title', video_id) if info else video_id)
        
        source_file = None
        source_format = 'm4a'
        for ext in ['m4a', 'webm', 'mp3', 'opus']:
            check_path = os.path.join(DOWNLOAD_DIR, f'chocotube_freeconvert_{unique_id}.{ext}')
            if os.path.exists(check_path):
                source_file = check_path
                source_format = ext
                break
        
        if not source_file:
            return jsonify({'success': False, 'error': 'ダウンロードに失敗しました'}), 500
        
        headers = {
            'Authorization': f'Bearer {FREECONVERT_API_KEY}',
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }
        
        import base64
        with open(source_file, 'rb') as f:
            file_data = base64.b64encode(f.read()).decode('utf-8')
        
        job_payload = {
            'tasks': {
                'import-1': {
                    'operation': 'import/base64',
                    'file': file_data,
                    'filename': f'audio.{source_format}'
                },
                'convert-1': {
                    'operation': 'convert',
                    'input': 'import-1',
                    'input_format': source_format,
                    'output_format': target_format,
                    'options': {
                        'audio_bitrate': '192'
                    }
                },
                'export-1': {
                    'operation': 'export/url',
                    'input': 'convert-1'
                }
            }
        }
        
        res = http_session.post(
            'https://api.freeconvert.com/v1/process/jobs',
            json=job_payload,
            headers=headers,
            timeout=120
        )
        
        if res.status_code in [200, 201]:
            job_data = res.json()
            job_id = job_data.get('id')
            
            for _ in range(60):
                time.sleep(2)
                status_res = http_session.get(
                    f'https://api.freeconvert.com/v1/process/jobs/{job_id}',
                    headers=headers,
                    timeout=30
                )
                if status_res.status_code == 200:
                    status = status_res.json()
                    if status.get('status') == 'completed':
                        tasks = status.get('tasks', {})
                        export_task = tasks.get('export-1', {})
                        if export_task.get('status') == 'completed':
                            result = export_task.get('result', {})
                            download_url = result.get('url')
                            if download_url:
                                if os.path.exists(source_file):
                                    os.remove(source_file)
                                return jsonify({
                                    'success': True,
                                    'url': download_url,
                                    'format': target_format,
                                    'title': title,
                                    'method': 'freeconvert'
                                })
                    elif status.get('status') == 'error':
                        break
            
            if os.path.exists(source_file):
                os.remove(source_file)
            return jsonify({'success': False, 'error': '変換がタイムアウトしました'}), 500
        else:
            if os.path.exists(source_file):
                os.remove(source_file)
            return jsonify({'success': False, 'error': 'FreeConvert APIエラー'}), 500
            
    except Exception as e:
        print(f"FreeConvert convert error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/convert/apify/<video_id>')
@login_required
def api_convert_apify(video_id):
    """Apify Audio File Converter APIを使用してファイル形式を変換"""
    target_format = request.args.get('format', 'mp3')
    
    if not APIFY_API_TOKEN:
        return jsonify({'success': False, 'error': 'Apify APIトークンが設定されていません'}), 400
    
    try:
        video_url = f"https://www.youtube.com/watch?v={video_id}"
        unique_id = f"{video_id}_{int(time.time())}"
        
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'format': 'bestaudio[ext=m4a]/bestaudio/best',
            'outtmpl': os.path.join(DOWNLOAD_DIR, f'chocotube_apify_{unique_id}.%(ext)s'),
            'http_headers': {'User-Agent': random.choice(USER_AGENTS)},
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=True)
            title = sanitize_filename(info.get('title', video_id) if info else video_id)
        
        source_file = None
        for ext in ['m4a', 'webm', 'mp3', 'opus']:
            check_path = os.path.join(DOWNLOAD_DIR, f'chocotube_apify_{unique_id}.{ext}')
            if os.path.exists(check_path):
                source_file = check_path
                break
        
        if not source_file:
            return jsonify({'success': False, 'error': 'ダウンロードに失敗しました'}), 500
        
        audio_stream_res = http_session.get(
            f'https://api.apify.com/v2/key-value-stores/temp/records/audio_{unique_id}',
            timeout=5
        )
        
        upload_headers = {
            'Content-Type': 'application/octet-stream'
        }
        
        with open(source_file, 'rb') as f:
            audio_data = f.read()
        
        apify_payload = {
            'audioUrl': f'https://www.youtube.com/watch?v={video_id}',
            'targetFormat': target_format
        }
        
        res = http_session.post(
            f'https://api.apify.com/v2/acts/akash9078~audio-file-converter/run-sync-get-dataset-items?token={APIFY_API_TOKEN}',
            json=apify_payload,
            headers={'Content-Type': 'application/json'},
            timeout=300
        )
        
        if os.path.exists(source_file):
            os.remove(source_file)
        
        if res.status_code == 200:
            result_data = res.json()
            if isinstance(result_data, list) and len(result_data) > 0:
                file_url = result_data[0].get('fileUrl')
                if file_url:
                    return jsonify({
                        'success': True,
                        'url': file_url,
                        'format': target_format,
                        'title': title,
                        'method': 'apify'
                    })
            return jsonify({'success': False, 'error': '変換結果が見つかりませんでした'}), 500
        else:
            return jsonify({'success': False, 'error': 'Apify APIエラー'}), 500
            
    except Exception as e:
        print(f"Apify convert error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.after_request
def add_header(response):
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
