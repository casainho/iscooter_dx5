#!/usr/bin/env python3
"""Build the local post table without modifying the archived records."""

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / "iscooter-posts"
OUTPUT = ARCHIVE / "table-index.md"
SYSTEM_EVENTS = {"2207593196715550", "2207594330048770"}
UNAVAILABLE = "Not identified"


def field(text: str, name: str) -> str:
    match = re.search(rf"(?im)^-\s*\*\*{re.escape(name)}:\*\*\s*(.+)$", text)
    return match.group(1).strip() if match else UNAVAILABLE


def cell(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().replace("\\", "\\\\").replace("|", "\\|")


def post_title(text: str) -> str:
    title = field(text, "Title")
    if title != UNAVAILABLE and not re.match(r"(?i)^(unknown|not (identified|verified|available))", title):
        return title
    match = re.search(r"(?ms)^## Original text\s*\n(.*?)(?=^#{1,3} |\Z)", text)
    if not match:
        return UNAVAILABLE
    for line in match.group(1).splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith((">", "#", "-", "*")) or re.match(
            r"(?i)^(original (message|text|language|polish|french)|not separated|not (available|identified|verified)|visible (english|capture)|unavailable|text unavailable|see (capture|below)|no (text|original|post text|author(?:-written)? text)|\[)", line
        ):
            return UNAVAILABLE
        excerpt = re.sub(r"\s+", " ", line)
        if len(excerpt) > 120:
            excerpt = excerpt[:117].rstrip() + "…"
        return f"Excerpt: {excerpt}"
    return UNAVAILABLE


def row(post_id: str, text: str) -> str:
    date = field(text, "Date")
    link = field(text, "Link")
    # A direct link must come from the local record and contain the same post ID.
    url = re.search(r"https?://[^\s)>]+", link)
    link_cell = f"[Facebook]({url.group(0)})" if url and re.search(rf"/posts/{post_id}(?:/|\?|$)", url.group(0)) else UNAVAILABLE
    return f"| {post_id} | {cell(date)} | {cell(post_title(text))} | {link_cell} |"


def main() -> None:
    records = []
    for folder in sorted(ARCHIVE.iterdir(), key=lambda p: p.name, reverse=True):
        if not folder.is_dir() or not folder.name.isdigit():
            continue
        record = folder / "post.md"
        records.append((folder.name, record.read_text(encoding="utf-8") if record.is_file() else ""))
    posts = [(pid, text) for pid, text in records if pid not in SYSTEM_EVENTS]
    events = [(pid, text) for pid, text in records if pid in SYSTEM_EVENTS]
    lines = [
        "# Local post table index", "",
        "Generated from locally archived records; this is not proof that the Facebook group is fully archived. Dates retain the recorded uncertainty. When no actual title is recorded, an excerpt of the original text is labelled as such; unavailable values are not guessed.", "",
        f"Original-post records: {len(posts)}. Known system events are listed separately below.", "",
        "| Post ID | Date (as recorded) | Title or excerpt | Direct link |",
        "|---|---|---|---|",
        *(row(pid, text) for pid, text in posts), "",
        "## Known Facebook system events", "",
        "These are not counted as original posts.", "",
        "| Event ID | Date (as recorded) | Title or excerpt | Direct link |",
        "|---|---|---|---|",
        *(row(pid, text) for pid, text in events), "",
    ]
    OUTPUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"Updated {OUTPUT}: {len(posts)} post records, {len(events)} system events.")


if __name__ == "__main__":
    main()
