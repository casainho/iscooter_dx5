#!/usr/bin/env python3
"""Synchronize the newest visible group posts and revisit incomplete records."""
import argparse, hashlib, json, re, subprocess, sys, time
from pathlib import Path
import background_partial_review as base
import audit_archive

STATE = base.ARCHIVE / 'sync-state.json'
POST_URL = f'https://www.facebook.com/groups/{base.GROUP}/posts/'

def save_state(state, message):
    temp = STATE.with_suffix('.tmp')
    temp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + '\n')
    temp.replace(STATE)
    with base.INDEX.open('a', encoding='utf-8') as f:
        f.write('\n- Sync ' + base.stamp() + ': ' + message.replace('\n', ' ') + '\n')
    subprocess.run([sys.executable, 'scripts/update_table_index.py'], cwd=base.ROOT, check=True, stdout=subprocess.DEVNULL)

def capture(client, page, post, ident, new):
    if not new:
        before = post.read_text(encoding='utf-8')
        status = re.search(r'(?im)^-\s*\*\*Status:\*\*\s*([^\n]+)', before)
        if status and status.group(1).strip().lower().startswith('complete'):
            raise RuntimeError('record is complete; refusing modification')
    data, total, filtering, unopened = base.read_post(client, page, ident)
    if not new:
        current = post.read_text(encoding='utf-8')
        status = re.search(r'(?im)^-\s*\*\*Status:\*\*\s*([^\n]+)', current)
        if status and status.group(1).strip().lower().startswith('complete'):
            raise RuntimeError('record is now complete; refusing modification')
    photos = base.save_images(post.parent, data.get('photos', []))
    title = re.search(r'^Publicação de (.+)$', data['text'], re.M)
    author = title.group(1) if title else 'unknown'
    note = ('Facebook displayed ' + (str(total) if total is not None else 'an unverified number of')
            + f' comments; {len(data["comments"])} articles captured; filter: {filtering}; '
            + ('reply controls remain unopened.' if unopened else 'no unopened reply controls observed; completeness not verified.'))
    if new:
        header = (f'# Post {ident}\n\n- **Author:** {author}\n- **Date:** unknown; not verified.\n'
                  f'- **Link:** {POST_URL}{ident}/\n- **Model:** not identified; not verified.\n'
                  f'- **Collected on:** {base.stamp()}\n- **Status:** partial — original text, dates, attachments and comment completeness require verification.\n\n'
                  '## Original text\n\nNot separated from the unverified dialog capture below.\n\n'
                  '## Comments and replies\n\n' + note + '\n\n## Images and attachments\n\n'
                  + ('\n'.join(photos) if photos else 'No images saved; attachment coverage not verified.')
                  + '\n\n## Collection notes\n\nRaw capture may contain translations, duplicates, UI text and unexpanded material. Videos were not downloaded.\n')
    else:
        header = '\n\n## Synchronization recheck — ' + base.stamp() + '\n\n' + note + '\n'
        if photos: header += '\n' + '\n'.join(photos) + '\n'
    # A repeated identical capture is not new data. URLs for image bytes may
    # expire, so compare visible content and stable image observations only.
    fingerprint = hashlib.sha256(json.dumps({'text':data['text'], 'comments':data['comments'],
        'photos':sorted(p.get('href', '') for p in data.get('photos', [])), 'count':total, 'filter':filtering, 'unopened':unopened},
        sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    marker = f'<!-- sync-capture-sha256: {fingerprint} -->'
    if not new:
        previous = post.read_text(encoding='utf-8')
        status = re.search(r'(?im)^-\s*\*\*Status:\*\*\s*([^\n]+)', previous)
        if status and status.group(1).strip().lower().startswith('complete'):
            raise RuntimeError('record is now complete; refusing modification')
        if marker in previous and not any(p.startswith('- [background-') for p in photos):
            return len(data['comments']), len(photos), False
    body = '\n' + marker + '\n\n### Unverified dialog capture\n\n' + data['text'] + '\n\n### Visible comment and reply articles\n\n'
    for c in data['comments']:
        body += '#### ' + (c.get('label') or 'Unlabelled') + ' — ' + (c.get('date') or 'date unverified') + '\n\n' + c.get('text','') + '\n\n'
    if new:
        # Exclusive creation protects even a record created by another process.
        with post.open('x', encoding='utf-8') as f: f.write(header + body)
    else:
        with post.open('a', encoding='utf-8') as f: f.write(header + body)
    return len(data['comments']), len(photos), True

def build_jobs(new_ids, audit_results):
    """Use local findings to select incomplete records without touching complete ones."""
    jobs = [(ident, True) for ident in new_ids]
    unresolved_complete = []
    for result in audit_results:
        if result['kind'] != 'original_post': continue
        post = base.ARCHIVE / result['id'] / 'post.md'
        text = post.read_text(errors='replace') if post.exists() else ''
        status = re.search(r'(?im)^-\s*\*\*Status:\*\*\s*([^\n]+)', text)
        if status and status.group(1).strip().lower().startswith('complete'):
            if result['issues']: unresolved_complete.append(result['id'])
            continue
        if result['issues'] or not status or not text.strip():
            jobs.append((result['id'], False))
    return jobs, unresolved_complete

def discover(client, page, known, limit):
    client.tool('navigate_page', {'pageId': page, 'type':'url', 'url': f'https://www.facebook.com/groups/{base.GROUP}/'})
    time.sleep(2)
    s = base.snapshot(client,page)
    m = re.search(r'uid=(\S+) button "ordenar feed do grupo por [^\"]+"', s)
    if not m: raise RuntimeError('Group feed or sorting control not available; check session and interface language')
    if 'Publicações novas' not in m.group(0):
        client.tool('click', {'pageId':page,'uid':m.group(1)})
        menu = base.snapshot(client,page)
        choice = re.search(r'uid=(\S+) menuitemradio "Publicações novas ',menu)
        if not choice: raise RuntimeError('New-posts sorting unavailable')
        client.tool('click', {'pageId':page,'uid':choice.group(1)})
    time.sleep(2)
    found, seen = [], set()
    for _ in range(8):
        s = base.snapshot(client,page)
        # A post timestamp is a link; ignore comment links and unrelated group content.
        links = re.findall(r'link "[^\"]+"(?: description="[^\"]*")? url="https://www\.facebook\.com/groups/'+base.GROUP+r'/posts/(\d+)/([^\"]*)"',s)
        boundary = False
        for ident, query in links:
            if 'comment_id=' in query or ident in seen: continue
            seen.add(ident)
            if ident in known:
                boundary = True
                continue
            if not boundary: found.append(ident)
        if boundary or len(found)>=limit: break
        client.tool('press_key', {'pageId':page,'key':'End'})
        time.sleep(2)
    return found[:limit]

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--limit', type=int, default=10, help='maximum new posts (default 10)')
    p.add_argument('--discover-only', action='store_true', help='read-only feed check; do not change archive')
    p.add_argument('--resume', action='store_true', help='resume pending jobs from an interrupted sync')
    p.add_argument('--audit-only', action='store_true', help='inspect local archive only; print per-record issues as JSON')
    args=p.parse_args()
    if args.limit < 1: p.error('--limit must be positive')
    if args.audit_only:
        if args.resume or args.discover_only: p.error('--audit-only cannot be combined with --resume or --discover-only')
        if not base.ARCHIVE.is_dir(): p.error('archive directory does not exist')
        records = audit_archive.audit(base.ARCHIVE)
        print(json.dumps({'summary':audit_archive.summary(records), 'records':records}, ensure_ascii=False, indent=2))
        return 0
    base.ARCHIVE.mkdir(exist_ok=True)
    audit_results = audit_archive.audit(base.ARCHIVE)
    print('Local audit:', json.dumps(audit_archive.summary(audit_results), ensure_ascii=False), flush=True)
    import fcntl
    with base.LOCK.open('a') as lock:
        try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: raise SystemExit('Another collector is running')
        if args.resume:
            if not STATE.exists(): raise SystemExit('No sync checkpoint to resume')
            state=json.loads(STATE.read_text())
            if state.get('status') not in ('stopped', 'running', 'starting') or 'jobs' not in state: raise SystemExit('No interrupted sync with pending jobs')
            args.limit=state['requested']
            state['status']='starting'; state.pop('error',None)
        else:
            state={'status':'starting','started':base.stamp(),'direction':'newer','requested':args.limit,'new':0,'updated':0,'failed':0,'last_id':None,'in_progress':None}
        if not args.discover_only: save_state(state, f'starting newer sync; requested {args.limit}; chronological coverage unverified')
        progress_stop=base.start_progress(state)
        client=None
        try:
            client=base.Client(); pages=client.tool('list_pages',{})
            matches=re.findall(r'(?m)^(\d+):[^\n]*facebook\.com/groups/'+base.GROUP+r'[^\n]*',pages)
            if not matches:
                client.tool('new_page', {'url':f'https://www.facebook.com/groups/{base.GROUP}/','background':True})
                pages=client.tool('list_pages',{})
                matches=re.findall(r'(?m)^(\d+):[^\n]*facebook\.com/groups/'+base.GROUP+r'[^\n]*',pages)
                if not matches: raise RuntimeError('Group tab could not be opened; login or authorization may be required')
            page=int(matches[0]); client.tool('select_page',{'pageId':page})
            folders={p.name for p in base.ARCHIVE.iterdir() if p.is_dir() and p.name.isdigit()}
            new_ids=[] if args.resume else discover(client,page,folders,args.limit)
            print('New candidate IDs:', ', '.join(new_ids) or 'none', flush=True)
            if args.discover_only: return 0
            state['status']='running'; save_state(state, f'new-posts feed inspected; {len(new_ids)} candidate IDs; ordering and gaps unverified')
            if args.resume:
                jobs=state['jobs']
            else:
                jobs, unresolved_complete = build_jobs(new_ids, audit_results)
                state['complete_with_audit_findings'] = unresolved_complete
                state['jobs']=jobs; save_state(state, f'queued {len(jobs)} new or incomplete records')
            for ident,is_new in jobs:
                folder=base.ARCHIVE/ident; post=folder/'post.md'
                if post.exists():
                    m=re.search(r'\*\*Status:\*\*\s*([^\n]+)',post.read_text(errors='replace'),re.I)
                    if m and m.group(1).strip().lower().startswith('complete'):
                        state['jobs']=state['jobs'][1:]; continue
                    is_new=False
                state['in_progress']=ident; save_state(state, f'in progress {ident}; last processed {state["last_id"]}')
                try:
                    folder.mkdir(exist_ok=True)
                    comments, images, changed=base.retry_record(
                        lambda: capture(client,page,post,ident,not post.exists()),
                        lambda attempt, exc: save_state(state, f'{ident}: attempt {attempt}/3 failed ({str(exc)[:200]}); retrying in 10 seconds'))
                    if changed: state['new' if is_new else 'updated']+=1
                    else: state['unchanged'] = state.get('unchanged', 0) + 1
                    state['last_id']=ident; state['in_progress']=None; state['jobs']=state['jobs'][1:]
                    save_state(state,f'{ident} {"saved partial" if changed else "unchanged"}: {comments} comment articles, {images} image observations; local findings require reconciliation')
                except Exception as e:
                    if any(x in str(e).lower() for x in ('login','authorisation','authorization','connection','automation','captcha')): raise
                    base.mark_attempts_exhausted(post, ident, str(e))
                    state['failed']+=1; state['last_id']=ident; state['in_progress']=None; state['jobs']=state['jobs'][1:]
                    state.setdefault('attempts_exhausted', {})[ident] = {'attempts': 3, 'error': str(e)[:250]}
                    save_state(state,f'{ident}: marked complete after 3 unsuccessful attempts: {str(e)[:250]}; content incomplete, future automatic retries skipped')
            state['status']='finished_with_partials'; save_state(state,f'pass ended; {state["new"]} new, {state["updated"]} existing updated, {state["failed"]} failed; reconciliation required')
            return 0
        except Exception as e:
            if not args.discover_only:
                state['status']='stopped'; state['error']=str(e)[:300]; save_state(state,f'stopped: {state["error"]}; last ID {state["last_id"]}')
            print('Stopped:',e,file=sys.stderr); return 1
        finally:
            progress_stop.set()
            if client: client.close()

if __name__=='__main__': sys.exit(main())
