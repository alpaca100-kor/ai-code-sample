from pathlib import Path
from html import escape

root = Path(".")
output = root / "index.html"

# Detect every top-level folder containing HTML files.
categories = []

for folder in sorted(root.iterdir(), key=lambda p: p.name.lower()):
    if not folder.is_dir() or folder.name.startswith("."):
        continue

    files = sorted(folder.glob("*.html"), key=lambda p: p.name.lower())

    if not files:
        continue

    categories.append((folder.name, files))

folder_items = []
file_panels = []

for index, (folder, files) in enumerate(categories):
    folder_id = f"folder-{index}"

    folder_items.append(
        f'''          <button class="folder-button{" active" if index == 0 else ""}" type="button"
            data-folder="{folder_id}" aria-controls="{folder_id}-panel"
            aria-selected="{"true" if index == 0 else "false"}">
            <span class="folder-name">{escape(folder)}</span>
            <span class="folder-count" aria-label="{len(files)}개">{len(files)}</span>
          </button>'''
    )

    file_items = []
    for path in files:
        relative = path.as_posix()
        file_items.append(
            f'''              <li>
                <a class="file-link" href="{escape(relative, quote=True)}" target="_blank" rel="noopener noreferrer">
                  {escape(path.name)}
                </a>
              </li>'''
        )

    file_panels.append(
        f'''          <section class="file-panel{" active" if index == 0 else ""}"
            id="{folder_id}-panel" data-panel="{folder_id}"
            aria-labelledby="{folder_id}-label">
            <h2 id="{folder_id}-label">{escape(folder)}</h2>
            <ul class="file-list">
{chr(10).join(file_items)}
            </ul>
          </section>'''
    )

folders_html = chr(10).join(folder_items)
panels_html = chr(10).join(file_panels)

template = f'''<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta name="description" content="AI Sample Code - AI 관련 웹 애플리케이션과 샘플 코드를 한곳에서 제공합니다.">
  <title>AI Sample Code</title>
  <style>
    :root {{
      --bg: #f5f7fb;
      --surface: rgba(255,255,255,.82);
      --text: #172033;
      --muted: #697386;
      --border: rgba(23,32,51,.08);
      --shadow: 0 18px 50px rgba(35,45,70,.09);
      --accent: #635bff;
      --accent-2: #8b5cf6;
      --radius-lg: 24px;
      --radius-md: 16px;
    }}
    * {{ box-sizing: border-box; }}
    html {{ scroll-behavior: smooth; }}
    body {{
      margin: 0;
      min-height: 100vh;
      color: var(--text);
      background:
        radial-gradient(circle at 10% 10%, rgba(99,91,255,.12), transparent 28%),
        radial-gradient(circle at 90% 15%, rgba(139,92,246,.10), transparent 25%),
        var(--bg);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI",
        "Noto Sans KR", "Malgun Gothic", sans-serif;
      line-height: 1.6;
    }}
    button, a {{ font: inherit; }}
    a {{ color: inherit; text-decoration: none; }}
    .container {{ width: min(1120px, calc(100% - 40px)); margin: 0 auto; }}
    header {{ padding: 72px 0 44px; }}
    .hero {{
      position: relative;
      overflow: hidden;
      padding: 52px;
      border: 1px solid rgba(255,255,255,.8);
      border-radius: var(--radius-lg);
      background: linear-gradient(135deg, rgba(255,255,255,.9), rgba(255,255,255,.68));
      box-shadow: var(--shadow);
      backdrop-filter: blur(18px);
      -webkit-backdrop-filter: blur(18px);
    }}
    .hero::before {{
      content: "";
      position: absolute;
      width: 220px;
      height: 220px;
      top: -120px;
      right: -80px;
      border-radius: 50%;
      background: linear-gradient(135deg, var(--accent), var(--accent-2));
      opacity: .12;
      filter: blur(8px);
    }}
    .eyebrow {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      margin-bottom: 14px;
      padding: 6px 12px;
      border-radius: 999px;
      color: var(--accent);
      background: rgba(99,91,255,.09);
      font-size: .78rem;
      font-weight: 700;
      letter-spacing: .08em;
      text-transform: uppercase;
    }}
    .eyebrow::before {{
      content: "";
      width: 7px;
      height: 7px;
      border-radius: 50%;
      background: currentColor;
      box-shadow: 0 0 0 4px rgba(99,91,255,.1);
    }}
    h1 {{
      position: relative;
      margin: 0;
      font-size: clamp(2.2rem, 5vw, 4rem);
      line-height: 1.05;
      letter-spacing: -.045em;
    }}
    .hero-description {{
      position: relative;
      max-width: 680px;
      margin: 20px 0 0;
      color: var(--muted);
      font-size: 1.05rem;
    }}
    main {{ padding: 0 0 80px; }}
    .browser {{
      display: grid;
      grid-template-columns: minmax(190px, 260px) minmax(0, 1fr);
      min-height: 420px;
      overflow: hidden;
      border: 1px solid var(--border);
      border-radius: var(--radius-lg);
      background: var(--surface);
      box-shadow: var(--shadow);
      backdrop-filter: blur(14px);
      -webkit-backdrop-filter: blur(14px);
    }}
    .folder-list {{
      padding: 20px 14px;
      border-right: 1px solid var(--border);
      background: rgba(255,255,255,.42);
    }}
    .folder-heading {{
      margin: 0 10px 12px;
      color: var(--muted);
      font-size: .78rem;
      font-weight: 700;
      letter-spacing: .08em;
      text-transform: uppercase;
    }}
    .folder-button {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      width: 100%;
      margin: 4px 0;
      padding: 11px 12px;
      border: 0;
      border-radius: 10px;
      color: var(--text);
      background: transparent;
      text-align: left;
      cursor: pointer;
      transition: background 160ms ease, color 160ms ease;
    }}
    .folder-name {{
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}
    .folder-count {{
      flex: 0 0 auto;
      min-width: 24px;
      padding: 2px 7px;
      border-radius: 999px;
      color: var(--muted);
      background: rgba(23,32,51,.06);
      font-size: .72rem;
      font-weight: 700;
      line-height: 1.4;
      text-align: center;
    }}
    .folder-button.active .folder-count {{
      color: var(--accent);
      background: rgba(99,91,255,.12);
    }}
    .folder-button:hover {{ background: rgba(99,91,255,.07); }}
    .folder-button.active {{
      color: var(--accent);
      background: rgba(99,91,255,.1);
      font-weight: 700;
    }}
    .file-content {{ padding: 28px; }}
    .file-panel {{ display: none; }}
    .file-panel.active {{ display: block; }}
    .file-panel h2 {{
      margin: 0 0 20px;
      font-size: 1.35rem;
      letter-spacing: -.025em;
    }}
    .file-list {{
      display: flex;
      flex-direction: column;
      gap: 8px;
      margin: 0;
      padding: 0;
      list-style: none;
    }}
    .file-link {{
      display: block;
      padding: 16px 18px;
      border: 1px solid var(--border);
      border-radius: var(--radius-md);
      background: rgba(255,255,255,.62);
      box-shadow: 0 6px 20px rgba(35,45,70,.04);
      transition: transform 160ms ease, border-color 160ms ease,
        box-shadow 160ms ease, background 160ms ease;
    }}
    .file-link:hover {{
      transform: translateY(-2px);
      border-color: rgba(99,91,255,.22);
      background: #fff;
      box-shadow: 0 10px 26px rgba(35,45,70,.08);
    }}
    footer {{
      padding: 26px 0 42px;
      color: var(--muted);
      text-align: center;
      font-size: .82rem;
    }}
    button:focus-visible, a:focus-visible {{
      outline: 3px solid rgba(99,91,255,.35);
      outline-offset: 3px;
    }}
    @media (max-width: 700px) {{
      header {{ padding-top: 32px; }}
      .hero {{ padding: 34px 28px; }}
      .browser {{ grid-template-columns: 1fr; }}
      .folder-list {{
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 4px;
        border-right: 0;
        border-bottom: 1px solid var(--border);
      }}
      .folder-heading {{ grid-column: 1 / -1; }}
      .file-content {{ padding: 22px; }}
    }}
    @media (max-width: 480px) {{
      .container {{ width: min(100% - 24px, 1120px); }}
      .hero {{ padding: 30px 22px; border-radius: 20px; }}
      h1 {{ font-size: 2.35rem; }}
      .hero-description {{ font-size: .94rem; }}
      .folder-list {{ grid-template-columns: 1fr; }}
    }}
    @media (prefers-reduced-motion: reduce) {{
      html {{ scroll-behavior: auto; }}
      *, *::before {{ transition: none !important; }}
    }}
  </style>
</head>
<body>
  <div class="container">
    <header>
      <div class="hero">
        <div class="eyebrow">AI Sample Code</div>
        <h1>AI Sample Code</h1>
        <p class="hero-description">
          AI를 활용해 제작한 웹 애플리케이션과 샘플 코드를 한곳에서 확인할 수 있습니다.
          원하는 프로젝트를 선택하여 바로 실행해 보세요.
        </p>
      </div>
    </header>

    <main>
      <div class="browser">
        <aside class="folder-list" aria-label="폴더 목록">
          <p class="folder-heading">Folders</p>
{folders_html}
        </aside>

        <div class="file-content">
{panels_html}
        </div>
      </div>
    </main>

    <footer>AI Sample Code · All projects</footer>
  </div>

  <script>
    const folderButtons = document.querySelectorAll(".folder-button");
    const filePanels = document.querySelectorAll(".file-panel");

    folderButtons.forEach((button) => {{
      button.addEventListener("click", () => {{
        const target = button.dataset.folder;

        folderButtons.forEach((item) => {{
          const active = item === button;
          item.classList.toggle("active", active);
          item.setAttribute("aria-selected", String(active));
        }});

        filePanels.forEach((panel) => {{
          panel.classList.toggle("active", panel.dataset.panel === target);
        }});
      }});
    }});
  </script>
</body>
</html>
'''

output.write_text(template, encoding="utf-8")
print(f"Generated {{output}}")
