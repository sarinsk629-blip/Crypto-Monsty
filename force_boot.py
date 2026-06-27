#!/usr/bin/env python3
# force_boot.py

from pathlib import Path

def main():
    print("⚡ Deploying Dynamic Import Force-Booter...")
    index_path = Path("phoenix-frontend/index.html")

    if not index_path.exists():
        print("❌ index.html not found!")
        return

    html = index_path.read_text(encoding="utf-8")

    # 1. Remove the old, silent module loader
    old_script = '<script type="module" src="./js/app/bootstrap.js"></script>'
    html = html.replace(old_script, '')

    # 2. Inject the aggressive dynamic loader
    loader = '''
    <script>
      // Create a massive on-screen terminal for boot logs
      document.body.innerHTML += '<div id="boot-log" style="color: #0f0; background: #000; font-family: monospace; padding: 20px; z-index: 9999; position: absolute; top: 0; left: 0; width: 100vw; height: 100vh; overflow: auto;">[SYSTEM] Initializing Dynamic Boot Sequence...<br></div>';
      
      function logBoot(msg) {
          document.getElementById('boot-log').innerHTML += msg + '<br><br>';
      }

      // Force the browser to manually load the bootstrap file and catch ANY error
      import('./js/app/bootstrap.js')
        .then(() => {
            logBoot('[SUCCESS] Engine loaded and executed successfully.');
            setTimeout(() => document.getElementById('boot-log').style.display = 'none', 1500);
        })
        .catch(err => {
            logBoot('<span style="color: #ff4444;">[FATAL CRASH] ' + err.name + ': ' + err.message + '</span>');
            if (err.message.includes('fetch') || err.message.includes('resolve')) {
                logBoot('<span style="color: #ffaa00;">[DIAGNOSIS] Vercel is case-sensitive. You have a typo in an import statement (e.g. importing AppShell.js instead of appShell.js).</span>');
            }
        });
    </script>
    '''

    if "Dynamic Boot Sequence" not in html:
        html = html.replace("</body>", loader + "\n</body>")
        index_path.write_text(html, encoding="utf-8")
        print("✔ Force-Booter injected.")
    else:
        print("⚡ Force-Booter already present.")

if __name__ == "__main__":
    main()
