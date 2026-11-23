#!/usr/bin/env python3
"""Inspect local archive integrity without changing any archive files."""
import argparse
import json
import re
from pathlib import Path
from urllib.parse import urlparse
from PIL import Image
import background_partial_review as base

IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp', '.tif', '.tiff'}
FIELDS = ('Author', 'Date', 'Link', 'Model', 'Collected on', 'Status')

def audit(archive):
    results = []
    for folder in sorted((p for p in archive.iterdir() if p.is_dir() and p.name.isdigit()), key=lambda p: int(p.name), reverse=True):
        ident, issues = folder.name, []
        record = folder / 'post.md'
        if not record.is_file():
            issues.append('Missing post.md')
            text = ''
        else:
            try:
                text = record.read_text(encoding='utf-8')
            except (OSError, UnicodeError) as e:
                text = ''
                issues.append(f'Cannot read post.md: {type(e).__name__}')
            if not text.strip(): issues.append('Empty or unreadable post.md')
            if text.strip():
                for field in FIELDS:
                    match = re.search(rf'(?im)^-\s*\*\*{re.escape(field)}:\*\*\s*(.*)$', text)
                    if not match or not match.group(1).strip():
                        issues.append(f'Missing {field} field')
                        continue
                    value = match.group(1).strip()
                    if field in ('Date', 'Status') and re.search(r'(?i)\b(unknown|unverified|not verified|uncertain|unidentified|not identified|in progress|partial|inaccessible)\b',value):
                        issues.append(f'{field} requires attention: {value[:180]}')
                    if field == 'Status' and not re.match(r'(?i)^(complete|partial|inaccessible)\b', value):
                        issues.append(f'Unrecognized Status: {value[:180]}')
                    if field == 'Link':
                        link = re.search(r'https?://[^\s)>]+',value)
                        if not link:
                            issues.append('Direct post link missing or unverified')
                        else:
                            u = urlparse(link.group(0))
                            if u.hostname not in ('facebook.com', 'www.facebook.com', 'web.facebook.com', 'm.facebook.com') or not re.fullmatch(rf'/groups/{base.GROUP}/posts/{ident}/?',u.path):
                                issues.append('Direct post link does not match folder ID or group')
                for section in ('Original text', 'Comments and replies', 'Images and attachments', 'Collection notes'):
                    if not re.search(rf'(?im)^##\s+{re.escape(section)}\s*$', text):
                        issues.append(f'Missing section: {section}')
                for target in re.findall(r'\]\(([^)]+)\)', text):
                    target = target.strip().strip('<>')
                    if '://' in target or target.startswith(('#', 'mailto:')): continue
                    target = target.split('#', 1)[0].split('?', 1)[0]
                    if Path(target).suffix.lower() not in IMAGE_EXTENSIONS: continue
                    path = (folder / target).resolve()
                    if not path.is_relative_to(folder.resolve()):
                        issues.append(f'Image link outside post folder: {target}')
                    elif not path.is_file():
                        issues.append(f'Missing linked image: {target}')
        for image in folder.iterdir():
            if image.is_file() and image.suffix.lower() in IMAGE_EXTENSIONS:
                try:
                    with Image.open(image) as im: im.verify()
                except (OSError, ValueError, SyntaxError) as e:
                    issues.append(f'Invalid image {image.name}: {type(e).__name__}')
        results.append({'id':ident,'kind':'system_event' if ident in base.EVENTS else 'original_post','issues':list(dict.fromkeys(issues))})
    return results

def summary(results):
    posts = [r for r in results if r['kind'] == 'original_post']
    events = [r for r in results if r['kind'] == 'system_event']
    return {'original_posts':len(posts),'posts_with_issues':sum(bool(r['issues']) for r in posts),
            'system_events':len(events),'events_with_issues':sum(bool(r['issues']) for r in events)}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json', action='store_true', help='include per-record issues in JSON')
    args = parser.parse_args()
    if not base.ARCHIVE.is_dir():
        parser.exit(1, 'Archive directory does not exist\n')
    results = audit(base.ARCHIVE)
    print(json.dumps({'summary':summary(results),'records':results} if args.json else summary(results), ensure_ascii=False, indent=2))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
