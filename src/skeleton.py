"""제안 구조 → 실제 골격(.md stub) 쓰기 (Stage 0).

stub = frontmatter(title/purpose/sources/status/generated_by) + outline이
빈 섹션 헤딩으로 들어간 본문. 내용은 채우지 않는다 — Stage A 또는 사람 몫.

기본은 dry-run(트리 문자열만). write=True여도 기존 파일은 절대 덮어쓰지
않고 skip으로 보고한다.
"""

import os


def render_tree(pages):
    """제안 pages의 폴더 트리 문자열 (미리보기용)."""
    lines = []
    last_dir = None
    for p in sorted(pages, key=lambda x: x["path"]):
        d, name = os.path.split(p["path"])
        if d != last_dir:
            lines.append(f"{d or '.'}/")
            last_dir = d
        mark = "" if p["status"] == "grounded" else "  [gap]"
        lines.append(f"  {name} — {p['title']}{mark}")
    return "\n".join(lines)


def render_stub(page, run_id=""):
    fm = ["---", f"title: {page['title']}", f"purpose: {page['purpose']}"]
    if page["sources"]:
        fm.append("sources:")
        fm.extend(f"  - {s}" for s in page["sources"])
    else:
        fm.append("sources: []")
    fm.append(f"status: {page['status']}")
    if run_id:
        fm.append(f"generated_by: wiki-optimizer stage0 {run_id}")
    fm.append("---")
    body = "\n\n".join(page["outline"]) if page["outline"] else ""
    note = "<!-- 내용 없음. Stage A로 채우거나 직접 작성. -->"
    return "\n".join(fm) + "\n\n" + (body + "\n\n" if body else "") + note + "\n"


def write_skeleton(pages, out_root, write=False, run_id=""):
    """골격을 out_root에 쓴다(또는 dry-run).

    반환: {"written": [...], "skipped": [...], "tree": "..."}
    """
    out_root = os.path.abspath(os.path.expanduser(out_root))
    written, skipped = [], []
    for p in pages:
        dest = os.path.join(out_root, p["path"])
        if os.path.exists(dest):
            skipped.append(p["path"])
            continue
        if write:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "w") as f:
                f.write(render_stub(p, run_id=run_id))
        written.append(p["path"])
    return {"written": written, "skipped": skipped, "tree": render_tree(pages)}
