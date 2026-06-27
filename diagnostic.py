#!/usr/bin/env python3
# diagnostic.py

from pathlib import Path

def main():
    index_path = Path("phoenix-frontend/index.html")
    if not index_path.exists():
        print("❌ index.html not found!")
        return

    html = index_path.read_text(encoding="utf-8")
    
    debug_script = '''
    <script>
      window.onerror = function(msg, url, line, col, error) {
        document.body.innerHTML = '<div style="color:#ff4444; padding:20px; font-family:monospace; background:#000; height:100vh; width:100vw; position:absolute; top:0; left:0; z-index:9999; word-wrap: break-word;">' + 
          '<h3>🚨 CRITICAL JS FAULT</h3>' +
          '<b>Error:</b> ' + msg + '<br><br>' +
          '<b>File:</b> ' + url + '<br>' +
          '<b>Line:</b> ' + line + ':' + col + '<br><br>' +
          '<b>Stack:</b> ' + (error ? error.stack : 'N/A') + 
          '</div>';
        return false;
      };
    </script>
    '''
    
    if "CRITICAL JS FAULT" not in html:
        # Inject right before the closing </head>
        if "</head>" in html:
            html = html.replace("</head>", debug_script + "</head>")
        else:
            html = debug_script + html
        
        index_path.write_text(html, encoding="utf-8")
        print("✔ Diagnostic interceptor injected into index.html")
    else:
        print("⚡ Interceptor already present.")

if __name__ == "__main__":
    main()
