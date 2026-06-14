#!/usr/bin/env python3
"""
Šifruje vygenerované HTML stránky pomocí AES-GCM s klíčem odvozeným
z hesla (PBKDF2). Heslo se čte ze SITE_PASSWORD env var.

Princip:
1. Vezme všechny .html soubory v public/ a jejich obsah zašifruje
2. Vytvoří nový wrapper HTML s login formem a JS, který:
   - Vezme heslo od uživatele
   - Odvodí AES klíč přes PBKDF2 (100k iterací, SHA-256)
   - Dešifruje obsah a vloží do DOM
   - Heslo se ukládá do sessionStorage, takže přechody mezi stránkami
     nevyžadují opakované zadávání
3. URL struktura zůstává stejná (index.html, page-2.html, ...)

Bezpečnost:
- "Slabou ochranu" — chrání před náhodnými návštěvníky, není to
  cryptografická ochrana. Útočník, který dostane HTML, může zkoušet
  hesla brute-force (PBKDF2 100k iterací zpomaluje to ale neudělá to
  nemožné).
- Default 100k PBKDF2 iterací = ~100ms na moderním stroji,
  útočník by zvládl ~10 hesel/s. Pro slabá hesla nedostatečné.
- DOPORUČUJI: dlouhé heslo (16+ znaků, neslovníkové)
"""
import os
import sys
import json
import base64
import hashlib
import secrets
from pathlib import Path
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC


PBKDF2_ITER = 100_000
SALT_LEN = 16
NONCE_LEN = 12


def derive_key(password: str, salt: bytes) -> bytes:
    """Odvodit 256-bit AES klíč z hesla pomocí PBKDF2."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=PBKDF2_ITER,
    )
    return kdf.derive(password.encode("utf-8"))


def encrypt_payload(plaintext: bytes, password: str) -> dict:
    """Zašifrovat obsah a vrátit dict se solí, nonce a ciphertextem (vše base64)."""
    salt = secrets.token_bytes(SALT_LEN)
    nonce = secrets.token_bytes(NONCE_LEN)
    key = derive_key(password, salt)
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(nonce, plaintext, None)
    return {
        "salt": base64.b64encode(salt).decode("ascii"),
        "nonce": base64.b64encode(nonce).decode("ascii"),
        "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
        "iter": PBKDF2_ITER,
    }


WRAPPER_TEMPLATE = """<!DOCTYPE html>
<html lang="cs">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Reality Hlídač – přístup omezen</title>
<style>
  :root {{
    --bg: #0f172a;
    --bg-card: #1e293b;
    --text: #e2e8f0;
    --text-dim: #94a3b8;
    --accent: #3b82f6;
    --accent-hover: #2563eb;
    --error: #ef4444;
    --border: #334155;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
    background: var(--bg);
    color: var(--text);
    min-height: 100vh;
  }}
  #login-container {{
    display: flex;
    align-items: center;
    justify-content: center;
    min-height: 100vh;
    padding: 1rem;
  }}
  #login-card {{
    background: var(--bg-card);
    padding: 2.5rem;
    border-radius: 12px;
    border: 1px solid var(--border);
    box-shadow: 0 10px 25px rgba(0,0,0,0.3);
    max-width: 400px;
    width: 100%;
  }}
  #login-card h1 {{
    margin: 0 0 0.5rem;
    font-size: 1.5rem;
    font-weight: 600;
  }}
  #login-card p {{
    margin: 0 0 1.5rem;
    color: var(--text-dim);
    font-size: 0.9rem;
  }}
  #login-form {{
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
  }}
  #password-input {{
    padding: 0.75rem 1rem;
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: 8px;
    color: var(--text);
    font-size: 1rem;
    outline: none;
    transition: border-color 0.2s;
  }}
  #password-input:focus {{
    border-color: var(--accent);
  }}
  #login-button {{
    padding: 0.75rem 1rem;
    background: var(--accent);
    color: white;
    border: none;
    border-radius: 8px;
    font-size: 1rem;
    font-weight: 500;
    cursor: pointer;
    transition: background 0.2s;
  }}
  #login-button:hover {{
    background: var(--accent-hover);
  }}
  #login-button:disabled {{
    opacity: 0.5;
    cursor: not-allowed;
  }}
  #error-message {{
    color: var(--error);
    font-size: 0.875rem;
    min-height: 1.25rem;
    margin-top: 0.25rem;
  }}
  #loading {{
    text-align: center;
    color: var(--text-dim);
    padding: 2rem;
  }}
</style>
</head>
<body>
<div id="login-container">
  <div id="login-card">
    <h1>🏠 Reality Hlídač</h1>
    <p>Zadejte heslo pro přístup ke stránce.</p>
    <form id="login-form">
      <input type="password" id="password-input" placeholder="Heslo" autofocus required>
      <button type="submit" id="login-button">Odemknout</button>
      <div id="error-message"></div>
    </form>
  </div>
</div>

<script>
// Zašifrovaný obsah stránky (base64)
const PAYLOAD = {payload_json};

// PBKDF2 + AES-GCM dešifrování
async function deriveKey(password, salt) {{
  const enc = new TextEncoder();
  const baseKey = await crypto.subtle.importKey(
    "raw",
    enc.encode(password),
    "PBKDF2",
    false,
    ["deriveKey"]
  );
  return crypto.subtle.deriveKey(
    {{
      name: "PBKDF2",
      salt: salt,
      iterations: PAYLOAD.iter,
      hash: "SHA-256",
    }},
    baseKey,
    {{ name: "AES-GCM", length: 256 }},
    false,
    ["decrypt"]
  );
}}

function b64decode(s) {{
  const bin = atob(s);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return bytes;
}}

async function decrypt(password) {{
  const salt = b64decode(PAYLOAD.salt);
  const nonce = b64decode(PAYLOAD.nonce);
  const ciphertext = b64decode(PAYLOAD.ciphertext);
  const key = await deriveKey(password, salt);
  const plaintextBuf = await crypto.subtle.decrypt(
    {{ name: "AES-GCM", iv: nonce }},
    key,
    ciphertext
  );
  return new TextDecoder().decode(plaintextBuf);
}}

// Pokus o dešifrování. Pokud úspěch, nahradí celý dokument.
async function tryDecrypt(password) {{
  try {{
    const html = await decrypt(password);
    // Uložit heslo do sessionStorage pro další stránky (paginace)
    sessionStorage.setItem("rh_pwd", password);
    // Nahradit celý document
    document.open();
    document.write(html);
    document.close();
    return true;
  }} catch (e) {{
    return false;
  }}
}}

// Pokud máme heslo v sessionStorage (např. po kliknutí na další stránku),
// zkus rovnou dešifrovat
(async () => {{
  const saved = sessionStorage.getItem("rh_pwd");
  if (saved) {{
    const ok = await tryDecrypt(saved);
    if (ok) return;
    // Pokud heslo nesedí (typicky šifrované jiným heslem), smaž ho
    sessionStorage.removeItem("rh_pwd");
  }}
}})();

// Login form handler
document.getElementById("login-form").addEventListener("submit", async (e) => {{
  e.preventDefault();
  const input = document.getElementById("password-input");
  const button = document.getElementById("login-button");
  const errEl = document.getElementById("error-message");
  const password = input.value;

  errEl.textContent = "";
  button.disabled = true;
  button.textContent = "Odemykám…";

  const ok = await tryDecrypt(password);
  if (!ok) {{
    errEl.textContent = "Špatné heslo.";
    button.disabled = false;
    button.textContent = "Odemknout";
    input.select();
  }}
}});
</script>
</body>
</html>
"""


def encrypt_file(html_path: Path, password: str) -> None:
    """Načte HTML soubor, zašifruje jeho obsah a přepíše soubor wrapperem."""
    content = html_path.read_bytes()
    payload = encrypt_payload(content, password)
    wrapped = WRAPPER_TEMPLATE.format(
        payload_json=json.dumps(payload, ensure_ascii=False)
    )
    html_path.write_text(wrapped, encoding="utf-8")


def main():
    password = os.environ.get("SITE_PASSWORD")
    if not password:
        sys.exit("❌ SITE_PASSWORD env variable není nastavená")

    public_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "public")
    if not public_dir.exists():
        sys.exit(f"❌ Adresář {public_dir} neexistuje")

    html_files = list(public_dir.glob("*.html"))
    if not html_files:
        sys.exit(f"❌ Žádné HTML soubory v {public_dir}")

    print(f"🔒 Šifruji {len(html_files)} HTML souborů…")
    for f in html_files:
        encrypt_file(f, password)
        print(f"   ✓ {f.name}")
    print(f"✅ Hotovo")


if __name__ == "__main__":
    main()
