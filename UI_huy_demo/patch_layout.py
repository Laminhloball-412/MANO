from pathlib import Path


path = Path(
    "/home/ubuntu/UI_huy_demo/static/css/app.css"
)

text = path.read_text(encoding="utf-8")
marker = "/* Layout fix: bottom navigation overlap */"

block = r'''

/* Layout fix: bottom navigation overlap */
.app-content {
  padding-bottom:
    calc(160px + env(safe-area-inset-bottom));
  scroll-padding-bottom:
    calc(120px + env(safe-area-inset-bottom));
}

.terminal {
  height: calc(100dvh - 350px);
  min-height: 260px;
  max-height: 540px;
  padding-bottom: 32px;
}

@media (min-width: 521px) {
  .terminal {
    height: calc(100dvh - 405px);
  }
}

@media (max-height: 700px) {
  .terminal {
    height: 300px;
    min-height: 240px;
  }
}
'''

if marker in text:
    raise SystemExit("Layout patch already exists")

path.write_text(
    text.rstrip() + block + "\n",
    encoding="utf-8"
)

print("Layout patch applied")
