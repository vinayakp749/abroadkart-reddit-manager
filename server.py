"""
AbroadKart Reddit Manager — Backend Server (Python/Flask)
─────────────────────────────────────────────────────────
Proxies all API calls so the browser never hits CORS restrictions.

Run:  python server.py
Then: http://localhost:3000
"""

import os, json, time, threading, urllib.request, urllib.parse, urllib.error
from flask import Flask, request, jsonify, send_from_directory, Response

app = Flask(__name__, static_folder='.')

# ── tiny CORS middleware ─────────────────────────────────────────────────────
@app.after_request
def add_cors(resp):
    resp.headers['Access-Control-Allow-Origin']  = '*'
    resp.headers['Access-Control-Allow-Headers'] = '*'
    resp.headers['Access-Control-Allow-Methods'] = 'GET,POST,OPTIONS'
    return resp

@app.route('/', defaults={'path': ''}, methods=['OPTIONS'])
@app.route('/<path:path>', methods=['OPTIONS'])
def options(_path=''):
    return '', 204

# ── helpers ──────────────────────────────────────────────────────────────────
def http_get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.status, json.loads(r.read().decode())

def http_post(url, data, headers=None):
    body = json.dumps(data).encode() if isinstance(data, dict) else data.encode()
    req  = urllib.request.Request(url, data=body, headers=headers or {}, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())

def http_post_form(url, params, headers=None):
    body = urllib.parse.urlencode(params).encode()
    req  = urllib.request.Request(url, data=body, headers=headers or {}, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())

def apify_token(req):
    return req.headers.get('X-Apify-Token') or os.getenv('APIFY_TOKEN', '')

def reddit_ua(username='abroadkart'):
    return os.getenv('REDDIT_USER_AGENT',
        f'AbroadKart:RedditManager:v1.0 (by /u/{username})')

# ─────────────────────────────────────────────────────────────────────────────
# HEALTH
# ─────────────────────────────────────────────────────────────────────────────
@app.route('/api/health')
def health():
    return jsonify({
        'status': 'ok',
        'time': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'env': {
            'apify':     bool(os.getenv('APIFY_TOKEN')),
            'anthropic': bool(os.getenv('ANTHROPIC_API_KEY')),
        }
    })

# ─────────────────────────────────────────────────────────────────────────────
# APIFY
# ─────────────────────────────────────────────────────────────────────────────
@app.route('/api/apify/me')
def apify_me():
    token = apify_token(request)
    if not token:
        return jsonify({'error': 'No Apify token'}), 400
    try:
        status, data = http_get('https://api.apify.com/v2/users/me',
                                {'Authorization': f'Bearer {token}'})
        return jsonify(data), status
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/apify/run', methods=['POST'])
def apify_run():
    token  = apify_token(request)
    body   = request.get_json()
    actor  = body.get('actor')
    inp    = body.get('input')
    to     = body.get('timeout', 300)

    if not token:  return jsonify({'error': 'No Apify token'}), 400
    if not actor:  return jsonify({'error': 'No actor'}), 400
    if not inp:    return jsonify({'error': 'No input'}), 400

    url = f'https://api.apify.com/v2/acts/{urllib.parse.quote(actor, safe="")}/runs?timeout={to}'
    try:
        status, data = http_post(url, inp, {
            'Authorization':  f'Bearer {token}',
            'Content-Type':   'application/json',
        })
        return jsonify(data), status
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/apify/runs/<run_id>')
def apify_run_status(run_id):
    token = apify_token(request)
    if not token: return jsonify({'error': 'No Apify token'}), 400
    try:
        status, data = http_get(f'https://api.apify.com/v2/actor-runs/{run_id}',
                                {'Authorization': f'Bearer {token}'})
        return jsonify(data), status
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/apify/datasets/<dataset_id>/items')
def apify_dataset(dataset_id):
    token  = apify_token(request)
    limit  = request.args.get('limit', 120)
    fields = request.args.get('fields', '')
    if not token: return jsonify({'error': 'No Apify token'}), 400

    url = f'https://api.apify.com/v2/datasets/{dataset_id}/items?limit={limit}'
    if fields:
        url += f'&fields={urllib.parse.quote(fields)}'
    try:
        status, data = http_get(url, {'Authorization': f'Bearer {token}'})
        return jsonify(data), status
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ─────────────────────────────────────────────────────────────────────────────
# REDDIT — OAuth token (script app, username + password)
# ─────────────────────────────────────────────────────────────────────────────
@app.route('/api/reddit/token', methods=['POST'])
def reddit_token():
    body          = request.get_json()
    client_id     = body.get('clientId', '')
    client_secret = body.get('clientSecret', '')
    username      = body.get('username', '')
    password      = body.get('password', '')

    if not all([client_id, client_secret, username, password]):
        return jsonify({'error': 'Missing credentials'}), 400

    import base64
    creds = base64.b64encode(f'{client_id}:{client_secret}'.encode()).decode()
    ua    = reddit_ua(username)

    try:
        status, data = http_post_form(
            'https://www.reddit.com/api/v1/access_token',
            {'grant_type': 'password', 'username': username, 'password': password},
            {
                'Authorization':  f'Basic {creds}',
                'User-Agent':     ua,
                'Content-Type':   'application/x-www-form-urlencoded',
            }
        )
        if 'error' in data:
            return jsonify({'error': data['error'], 'message': data.get('message','')}), 401

        return jsonify({
            'access_token': data['access_token'],
            'token_type':   data['token_type'],
            'expires_in':   data['expires_in'],
            'scope':        data.get('scope', ''),
            'username':     username,
            'userAgent':    ua,
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ─────────────────────────────────────────────────────────────────────────────
# REDDIT — Verify logged-in user
# ─────────────────────────────────────────────────────────────────────────────
@app.route('/api/reddit/me')
def reddit_me():
    auth = request.headers.get('Authorization', '')
    ua   = request.headers.get('X-Reddit-Useragent', 'AbroadKart:v1.0')
    if not auth: return jsonify({'error': 'No token'}), 400
    try:
        status, data = http_get('https://oauth.reddit.com/api/v1/me',
                                {'Authorization': auth, 'User-Agent': ua})
        return jsonify(data), status
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ─────────────────────────────────────────────────────────────────────────────
# REDDIT — Post a comment
# ─────────────────────────────────────────────────────────────────────────────
@app.route('/api/reddit/comment', methods=['POST'])
def reddit_comment():
    auth = request.headers.get('Authorization', '')
    ua   = request.headers.get('X-Reddit-Useragent', 'AbroadKart:v1.0')
    body = request.get_json()
    thing_id = body.get('thingId', '')
    text     = body.get('text', '')

    if not auth:     return jsonify({'error': 'No Reddit token'}), 400
    if not thing_id: return jsonify({'error': 'Missing thingId'}), 400
    if not text:     return jsonify({'error': 'Missing text'}), 400

    try:
        status, data = http_post_form(
            'https://oauth.reddit.com/api/comment',
            {'api_type': 'json', 'thing_id': thing_id, 'text': text},
            {
                'Authorization':  auth,
                'User-Agent':     ua,
                'Content-Type':   'application/x-www-form-urlencoded',
            }
        )
        errors = data.get('json', {}).get('errors', [])
        if errors:
            return jsonify({'error': errors[0][1], 'code': errors[0][0]}), 400
        return jsonify({'success': True, 'data': data.get('json', {}).get('data')})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ─────────────────────────────────────────────────────────────────────────────
# REDDIT — Resolve post thingId from URL
# ─────────────────────────────────────────────────────────────────────────────
@app.route('/api/reddit/post-info')
def reddit_post_info():
    import re
    auth = request.headers.get('Authorization', '')
    ua   = request.headers.get('X-Reddit-Useragent', 'AbroadKart:v1.0')
    url  = request.args.get('url', '')

    if not url: return jsonify({'error': 'No URL'}), 400

    m = re.search(r'reddit\.com/r/([^/]+)/comments/([a-z0-9]+)', url, re.I)
    if not m: return jsonify({'error': 'Cannot parse Reddit URL'}), 400

    subreddit, post_id = m.group(1), m.group(2)
    thing_id = f't3_{post_id}'

    if auth:
        try:
            api_url = f'https://oauth.reddit.com/r/{subreddit}/comments/{post_id}.json?limit=1'
            status, data = http_get(api_url, {'Authorization': auth, 'User-Agent': ua})
            post = data[0]['data']['children'][0]['data'] if data else {}
            return jsonify({
                'thingId':     thing_id,
                'postId':      post_id,
                'subreddit':   subreddit,
                'title':       post.get('title'),
                'author':      post.get('author'),
                'score':       post.get('score'),
                'numComments': post.get('num_comments'),
                'locked':      post.get('locked'),
                'archived':    post.get('archived'),
            })
        except Exception:
            pass

    return jsonify({'thingId': thing_id, 'postId': post_id, 'subreddit': subreddit})

# ─────────────────────────────────────────────────────────────────────────────
# ANTHROPIC — Generate AI draft reply
# ─────────────────────────────────────────────────────────────────────────────
@app.route('/api/ai/draft', methods=['POST'])
def ai_draft():
    api_key = request.headers.get('X-Anthropic-Key') or os.getenv('ANTHROPIC_API_KEY', '')
    body    = request.get_json()
    title   = body.get('title', '')
    post_body = body.get('body', '')[:800]
    subreddit = body.get('subreddit', 'unknown')
    persona   = body.get('persona', '')

    if not api_key: return jsonify({'error': 'No Anthropic API key'}), 400
    if not title:   return jsonify({'error': 'No post title'}), 400

    system = persona or (
        'You are the founder of AbroadKart, a study abroad guidance platform. '
        'Write a helpful, genuine Reddit reply (120-180 words) that directly answers the question. '
        'Sound like a knowledgeable community member, not an ad. '
        'Only mention AbroadKart if it naturally and genuinely helps. '
        'Never use bullet points — write in a conversational, human tone.'
    )

    user_msg = (
        f'Reddit post from {subreddit}:\n'
        f'Title: {title}\n'
        + (f'Body: {post_body}\n' if post_body else '') +
        '\nWrite a helpful reply. Reply with ONLY the comment text.'
    )

    try:
        status, data = http_post(
            'https://api.anthropic.com/v1/messages',
            {
                'model':      'claude-haiku-4-5-20251001',
                'max_tokens': 400,
                'system':     system,
                'messages':   [{'role': 'user', 'content': user_msg}],
            },
            {
                'x-api-key':         api_key,
                'anthropic-version': '2023-06-01',
                'content-type':      'application/json',
            }
        )
        if status != 200:
            return jsonify({'error': data.get('error', {}).get('message', 'Anthropic error')}), status
        return jsonify({'draft': data['content'][0]['text']})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ─────────────────────────────────────────────────────────────────────────────
# SERVE FRONTEND (index.html from same folder)
# ─────────────────────────────────────────────────────────────────────────────
@app.route('/')
def index():
    if os.path.exists(os.path.join(app.static_folder, 'index.html')):
        return send_from_directory(app.static_folder, 'index.html')
    return (
        '<h2>✅ AbroadKart Backend is running!</h2>'
        '<p>Place <code>index.html</code> in this folder to serve the frontend.</p>'
        '<p>API: <code>/api/health</code> | <code>/api/apify/*</code> | '
        '<code>/api/reddit/*</code> | <code>/api/ai/draft</code></p>'
    )

@app.route('/<path:filename>')
def static_files(filename):
    return send_from_directory(app.static_folder, filename)

# ─────────────────────────────────────────────────────────────────────────────
# START
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    port = int(os.getenv('PORT', 3000))
    print(f'\n🚀  AbroadKart Reddit Manager  →  http://localhost:{port}')
    print('\n📋  API endpoints:')
    print('    GET  /api/health')
    print('    GET  /api/apify/me              ← verify Apify token')
    print('    POST /api/apify/run             ← start Reddit scraper')
    print('    GET  /api/apify/runs/:id        ← poll run status')
    print('    GET  /api/apify/datasets/:id    ← fetch scraped posts')
    print('    POST /api/reddit/token          ← get Reddit OAuth token')
    print('    GET  /api/reddit/me             ← verify Reddit account')
    print('    GET  /api/reddit/post-info      ← resolve thingId from URL')
    print('    POST /api/reddit/comment        ← post a reply')
    print('    POST /api/ai/draft              ← generate AI reply')
    print(f'\n✅  Open http://localhost:{port}\n')
    app.run(host='0.0.0.0', port=port, debug=False)
