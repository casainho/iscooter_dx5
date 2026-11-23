#!/usr/bin/env python3
"""Conservative, resumable collection of supplementary data from the open group tab."""
import datetime, hashlib, io, json, os, re, select, shutil, subprocess, sys, threading, time, urllib.parse, urllib.request
import contextlib
if os.name == 'nt':
    import msvcrt
from pathlib import Path
from zoneinfo import ZoneInfo
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
ARCHIVE = ROOT / 'iscooter-posts'
INDEX = ARCHIVE / 'collection-index.md'
STATE = ARCHIVE / 'background-review-state.json'
LOCK = ARCHIVE / 'background-review.lock'
MCP = os.environ.get('ISCOOTER_CHROME_DEVTOOLS_MCP') or shutil.which('chrome-devtools-mcp') or '/home/cas/.npm/_npx/15c61037b1978c83/node_modules/.bin/chrome-devtools-mcp'
GROUP = '2207593166715553'
EVENTS = {'2207593196715550', '2207594330048770'}


@contextlib.contextmanager
def exclusive_lock(path):
    """Hold a non-blocking exclusive process lock across supported OSes."""
    if os.name == 'nt':
        import msvcrt
    path.parent.mkdir(exist_ok=True)
    mode = 'a+b' if os.name == 'nt' else 'a+'
    with path.open(mode) as lock:
        try:
            if os.name == 'nt':
                lock.seek(0, os.SEEK_END)
                if lock.tell() == 0:
                    lock.write(b'\0')
                    lock.flush()
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError) as exc:
            if isinstance(exc, BlockingIOError) or isinstance(exc, PermissionError) or getattr(exc, 'winerror', None) in (32, 33, 36):
                raise BlockingIOError('lock is already held') from exc
            raise
        try:
            yield lock
        finally:
            if os.name == 'nt':
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

def stamp():
    return datetime.datetime.now(ZoneInfo('Europe/Lisbon')).strftime('%Y-%m-%d %H:%M:%S %Z')

def start_progress(state):
    """Print a periodic snapshot even while a browser operation is waiting."""
    stop = threading.Event()
    def report():
        while not stop.wait(60):
            counts = (f'novas={state.get("new", 0)} | atualizadas={state.get("updated", 0)} | '
                      f'falhas={state.get("failed", 0)}' if 'new' in state else
                      f'capturas={state.get("processed", 0)}')
            print(f'{stamp()} | estado={state.get("status", "unknown")} | {counts} | '
                  f'último={state.get("last_id") or "nenhum"} | '
                  f'em curso={state.get("in_progress") or "nenhum"}', flush=True)
    threading.Thread(target=report, daemon=True).start()
    return stop

def retry_record(operation, on_retry):
    """Try a record at most three times; never retry authentication barriers."""
    for attempt in range(1, 4):
        try:
            return operation()
        except Exception as exc:
            if attempt == 3 or any(x in str(exc).lower() for x in
                                   ('login', 'authorisation', 'authorization', 'connection', 'automation', 'captcha')):
                raise
            on_retry(attempt, exc)
            time.sleep(10)

def mark_attempts_exhausted(post, ident, error):
    """Close future retries while retaining the actual collection limitation."""
    if post.exists():
        content = post.read_text(encoding='utf-8')
        status = re.search(r'(?im)^-\s*\*\*Status:\*\*\s*([^\n]+)', content)
        if status and status.group(1).strip().lower().startswith('complete'):
            return
        line = '- **Status:** complete — three failed attempts; content remains incomplete or inaccessible and will not be retried automatically.'
        if status:
            content = content[:status.start()] + line + content[status.end():]
        else:
            content += '\n\n' + line + '\n'
    else:
        post.parent.mkdir(exist_ok=True)
        content = (f'# Post {ident}\n\n- **Author:** unknown\n- **Date:** unknown; not verified.\n'
                   f'- **Link:** https://www.facebook.com/groups/{GROUP}/posts/{ident}/\n'
                   f'- **Model:** not identified.\n- **Collected on:** {stamp()}\n'
                   '- **Status:** complete — three failed attempts; content inaccessible and will not be retried automatically.\n\n'
                   '## Original text\n\nNot obtained.\n\n## Comments and replies\n\nNot obtained.\n\n'
                   '## Images and attachments\n\nNot obtained.\n\n## Collection notes\n')
    content += f'\n\nCollection attempts exhausted on {stamp()}: {error[:300]}. Content completeness was not established.\n'
    post.write_text(content, encoding='utf-8')

def checkpoint(state, note):
    state['updated'] = stamp()
    tmp = STATE.with_suffix('.tmp')
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + '\n')
    tmp.replace(STATE)
    with INDEX.open('a') as f:
        f.write('\n- Background review ' + stamp() + ': ' + note.replace('\n', ' ') + '\n')
    subprocess.run([sys.executable, 'scripts/update_table_index.py'], cwd=ROOT, check=True, stdout=subprocess.DEVNULL)

class Client:
    def __init__(self):
        self.p = subprocess.Popen([MCP, '--autoConnect', '--no-usage-statistics', '--no-performance-crux'], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)
        self.n = 0
        self.call('initialize', {'protocolVersion':'2025-03-26','capabilities':{},'clientInfo':{'name':'iscooter-background-review','version':'1.0'}}, 20)
        self.send({'jsonrpc':'2.0','method':'notifications/initialized'})
    def send(self, obj):
        self.p.stdin.write(json.dumps(obj, ensure_ascii=False) + '\n'); self.p.stdin.flush()
    def call(self, method, params, timeout=60):
        self.n += 1; ident = self.n
        self.send({'jsonrpc':'2.0','id':ident,'method':method,'params':params})
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if not select.select([self.p.stdout], [], [], max(0, end-time.monotonic()))[0]: break
            line = self.p.stdout.readline()
            if not line: raise RuntimeError('browser automation process ended')
            msg = json.loads(line)
            if msg.get('id') != ident: continue
            if 'error' in msg: raise RuntimeError(str(msg['error']))
            result = msg.get('result', {})
            if result.get('isError'): raise RuntimeError(' '.join(c.get('text','') for c in result.get('content',[]))[:300])
            return result
        raise TimeoutError('browser automation did not respond')
    def tool(self, name, args):
        r = self.call('tools/call', {'name':name,'arguments':args})
        return '\n'.join(c.get('text','') for c in r.get('content',[]) if c.get('type') == 'text')
    def close(self):
        self.p.terminate()

def snapshot(client, page):
    return client.tool('take_snapshot', {'pageId':page})

def read_post(client, page, ident):
    client.tool('navigate_page', {'pageId':page,'type':'url','url':f'https://www.facebook.com/groups/{GROUP}/posts/{ident}/'})
    time.sleep(2)
    s = snapshot(client, page)
    if not re.search(r'dialog "Publicação de ', s):
        raise RuntimeError('post dialog not accessible; login, authorisation or loading failure possible')
    # These controls reveal text only. Never activate reaction, reply, share or submit controls.
    m = re.search(r'uid=(\S+) button "Ver original"', s)
    if m:
        client.tool('click', {'pageId':page,'uid':m.group(1)})
        s = snapshot(client, page)
    # Only the first post-level See more, if present before the comment action.
    pre = s.split('button "Comentar a publicação', 1)[0]
    m = re.search(r'uid=(\S+) button "Ver mais"', pre)
    if m:
        client.tool('click', {'pageId':page,'uid':m.group(1)})
        s = snapshot(client, page)
    m = re.search(r'uid=(\S+) button "Mais relevantes[^\"]*"', s)
    if m:
        client.tool('click', {'pageId':page,'uid':m.group(1)})
        menu = snapshot(client, page)
        a = re.search(r'uid=(\S+) menuitem "Todos os comentários ', menu)
        if a: client.tool('click', {'pageId':page,'uid':a.group(1)})
    time.sleep(1)
    for _ in range(30):
        s = snapshot(client, page)
        m = re.search(r'uid=(\S+) button "Ver (?:\d+\s*)?respostas?"', s)
        if not m: break
        client.tool('click', {'pageId':page,'uid':m.group(1)})
    s = snapshot(client, page)
    js = r'''() => { const d=document.querySelector('[role=dialog]'); if(!d) return null; return {text:d.innerText, comments:[...d.querySelectorAll('[role=article][aria-label]')].map(e=>({label:e.getAttribute('aria-label'),text:e.innerText,date:[...e.querySelectorAll('a[aria-label]')].map(a=>a.getAttribute('aria-label')).find(x=>/202[0-9]/.test(x))})), photos:[...d.querySelectorAll('a[href*="photo"]')].map(a=>({href:a.href.split('&__cft__')[0],src:a.querySelector('img')?.src,comment:!!a.closest('[role=article][aria-label]')})).filter(x=>x.src)} }'''
    result = client.tool('evaluate_script', {'pageId':page,'function':js,'waitForStableDom':False})
    m = re.search(r'```json\n([\s\S]*?)\n```', result)
    if not m: raise RuntimeError('visible dialog could not be extracted')
    data = json.loads(m.group(1))
    if not data or not data.get('text'): raise RuntimeError('empty or loading post')
    count = re.search(r'Comentar a publicação de [^\"]*, (\d+)\s*comentários?', s)
    return data, int(count.group(1)) if count else None, ('All comments' if 'button "Todos os comentários' in s else 'not verified'), bool(re.search(r'button "Ver (?:\d+\s*)?respostas?"',s))

def save_images(folder, photos):
    out = []
    existing = {hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.iterdir() if p.is_file() and p.suffix.lower() in {'.jpg','.jpeg','.png','.webp'}}
    for photo in photos:
        try:
            u = urllib.parse.urlparse(photo['src'])
            if u.scheme != 'https' or not (u.hostname or '').endswith('.fbcdn.net'): continue
            request = urllib.request.Request(photo['src'], headers={'User-Agent':'Mozilla/5.0'})
            with urllib.request.urlopen(request, timeout=20) as response: blob = response.read(15_000_001)
            if len(blob) > 15_000_000: continue
            im = Image.open(io.BytesIO(blob)); im.verify()
            im = Image.open(io.BytesIO(blob)); width,height = im.size
            digest = hashlib.sha256(blob).hexdigest()
            if digest in existing:
                out.append(f"- Existing image duplicate, {width} × {height}; {'comment' if photo['comment'] else 'post'}; [Facebook photo]({photo['href']}).")
                continue
            ext = {'JPEG':'.jpg','PNG':'.png','WEBP':'.webp'}.get(im.format)
            if not ext: continue
            name = 'background-' + digest[:12] + ext
            (folder/name).write_bytes(blob); existing.add(digest)
            out.append(f"- [{name}]({name}) — {'comment' if photo['comment'] else 'post'} image, {width} × {height}; reduced resolution possible; [Facebook photo]({photo['href']}).")
        except Exception as e:
            out.append(f"- Photo download failed ({type(e).__name__}); [Facebook photo]({photo['href']}).")
    return out

def main():
    ARCHIVE.mkdir(exist_ok=True)
    try:
        with exclusive_lock(LOCK):
            previous = json.loads(STATE.read_text()) if STATE.exists() else {}
            state = {'status':'starting','started':stamp(),'pid':os.getpid(),'processed':previous.get('processed', 0),'last_id':previous.get('last_id'),'limitations':'Supplementary captures require reconciliation; complete records are never modified.'}
            checkpoint(state, 'starting continuous review of partial records; requested all accessible records')
            progress_stop = start_progress(state)
            client = None
            try:
                client = Client()
                pages = client.tool('list_pages', {})
                matches = re.findall(r'(?m)^(\d+):[^\n]*facebook\.com/groups/' + GROUP + r'[^\n]*', pages)
                if not matches: raise RuntimeError('authenticated group tab not found')
                page = int(matches[0]); client.tool('select_page', {'pageId':page})
                records = []
                for p in ARCHIVE.glob('*/post.md'):
                    if p.parent.name.isdigit() and p.parent.name not in EVENTS: records.append(p)
                records.sort(key=lambda p:int(p.parent.name), reverse=True)
                if state['last_id'] and any(p.parent.name == state['last_id'] for p in records):
                    records = records[next(i for i,p in enumerate(records) if p.parent.name == state['last_id'])+1:]
                state['status'] = 'running'; checkpoint(state, f'group tab selected; {len(records)} original-post records inventoried; complete records will be skipped')
                for post in records:
                    text = post.read_text(errors='replace')
                    status = re.search(r'\*\*Status:\*\*\s*([^\n]+)', text, re.I)
                    if status and status.group(1).strip().lower().startswith('complete'): continue
                    ident = post.parent.name
                    state['in_progress'] = ident; checkpoint(state, f'in progress: {ident}; previous completed ID {state["last_id"] or "none"}')
                    try:
                        data, total, filtering, unopened = retry_record(
                            lambda: read_post(client, page, ident),
                            lambda attempt, exc: checkpoint(state, f'{ident}: attempt {attempt}/3 failed ({str(exc)[:200]}); retrying in 10 seconds'))
                    except Exception as e:
                        error = str(e)[:300]
                        if any(x in error.lower() for x in ('connection', 'login', 'authorisation', 'authorization', 'automation', 'captcha')):
                            raise
                        state['last_id'] = ident; state['in_progress'] = None
                        mark_attempts_exhausted(post, ident, error)
                        state.setdefault('attempts_exhausted', {})[ident] = {'attempts': 3, 'error': error}
                        checkpoint(state, f'{ident}: marked complete after 3 unsuccessful attempts ({error}); content incomplete, future automatic retries skipped')
                        continue
                    images = save_images(post.parent, data.get('photos', []))
                    section = f'\n\n## Background recheck — {stamp()}\n\nSupplementary, unreviewed capture; original data retained. Filter: {filtering}; Facebook count: {total if total is not None else "not verified"}; {len(data["comments"])} comment/reply articles visible. Unopened reply controls: {"yes" if unopened else "none observed"}. UI labels, translations, missing content, duplicate captures and uncertain hierarchy may appear. Image contents not interpreted. This capture does not establish completeness; status remains partial.\n\n### Visible dialog capture\n\n' + data['text'] + '\n\n### Comment and reply articles\n\n'
                    for c in data['comments']:
                        section += '#### ' + (c.get('label') or 'Unlabelled') + ' — ' + (c.get('date') or 'date not verified') + '\n\n' + c.get('text','') + '\n\n'
                    section += '### Images observed\n\n' + ('\n'.join(images) if images else 'No downloadable post or comment images observed; attachment coverage not verified.') + '\n'
                    with post.open('a') as f: f.write(section)
                    state['processed'] += 1; state['last_id'] = ident; state['in_progress'] = None
                    checkpoint(state, f'{ident}: supplementary capture saved; {len(data["comments"])} visible of {total if total is not None else "unverified"} indicated; filter {filtering}; {len(images)} image observations; record remains partial pending reconciliation')
                    time.sleep(2)
                state['status'] = 'finished_with_partials'; checkpoint(state, f'pass ended after {state["processed"]} records; supplementary captures need manual reconciliation and do not make records complete')
            except Exception as e:
                state['status'] = 'stopped'; state['error'] = str(e)[:300]
                checkpoint(state, f'stopped: {state["error"]}; last ID {state["last_id"]}; in progress {state.get("in_progress")}')
                print('Stopped:', state['error'], flush=True); return 1
            finally:
                progress_stop.set()
                if client: client.close()
    except BlockingIOError:
        print('Another background review is running')
        return 1
    return 0

if __name__ == '__main__': sys.exit(main())
