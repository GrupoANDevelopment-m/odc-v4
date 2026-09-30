"""Single chat test — usage: python scripts/single_chat_test.py 'your question here'"""
import json
import sys
import urllib.request

PORT = int(open("/tmp/odc_port").read().strip())
BASE = f"http://127.0.0.1:{PORT}"


def chat(text, timeout=180):
    req = urllib.request.Request(
        f"{BASE}/api/chat",
        data=json.dumps({"text": text}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        r = urllib.request.urlopen(req, timeout=timeout)
        return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}: {e.read().decode()[:300]}"}
    except Exception as e:
        return {"error": str(e)[:300]}


if __name__ == "__main__":
    text = sys.argv[1] if len(sys.argv) > 1 else "Olá!"
    print(f"\n>>> YOU: {text}\n")
    r = chat(text)
    if "error" in r:
        print(f"!!! ERROR: {r['error']}")
    else:
        print(f"<<< ODC:\n{r.get('report', '(no report)')}")
        print(f"\n[turns={r.get('turns')}  tool_calls={r.get('tool_calls')}  tools={r.get('tools')}]")
