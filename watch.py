import os
import time
import requests
from datetime import datetime

# --- config -----------------------------------------------------------------
RPC_URL = "https://api.mainnet-beta.solana.com"   # free public RPC
POLL_INTERVAL = 30                                 # seconds
WALLETS_FILE = "wallets.txt"
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# simple in‑memory cache for token symbol / price
token_cache = {}   # mint -> {"symbol": str, "priceUsd": float|None}


# --- helpers ----------------------------------------------------------------
def load_wallets():
    wallets = []
    with open(WALLETS_FILE, "r") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                wallets.append(line)
    return wallets


def rpc_call(method, params):
    """POST to Solana JSON‑RPC, return the result or None on failure."""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params,
    }
    try:
        r = requests.post(RPC_URL, json=payload, timeout=10)
        if r.status_code == 429:          # rate‑limit – wait a bit
            print("RPC rate‑limit, sleeping 10s…")
            time.sleep(10)
            r = requests.post(RPC_URL, json=payload, timeout=10)   # retry once
        r.raise_for_status()
        data = r.json()
        if "error" in data:
            print(f"RPC error: {data['error']}")
            return None
        return data.get("result")
    except Exception as e:
        print(f"RPC request failed: {e}")
        return None


def get_signatures(wallet, limit=5):
    """Return the most recent signatures for a wallet."""
    res = rpc_call(
        "getSignaturesForAddress",
        [wallet, {"limit": limit}],
    )
    if res:
        return [item["signature"] for item in res]
    return []


def get_transaction(signature):
    """Fetch a parsed transaction, including token balance changes."""
    return rpc_call(
        "getTransaction",
        [
            signature,
            {
                "encoding": "jsonParsed",
                "maxSupportedTransactionVersion": 0,
            },
        ],
    )


def get_token_info(mint):
    """Look up symbol and USD price on DexScreener (cached)."""
    if mint in token_cache:
        return token_cache[mint]

    url = f"https://api.dexscreener.com/tokens/v1/solana/{mint}"
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        pairs = r.json()
        if pairs and isinstance(pairs, list):
            pair = pairs[0]
            symbol = pair.get("baseToken", {}).get("symbol", "UNKNOWN")
            price_str = pair.get("priceUsd")
            price = float(price_str) if price_str else None
            token_cache[mint] = {"symbol": symbol, "priceUsd": price}
            return token_cache[mint]
    except Exception as e:
        print(f"DexScreener error for {mint}: {e}")

    # fallback so the script keeps running
    token_cache[mint] = {"symbol": "UNKNOWN", "priceUsd": None}
    return token_cache[mint]


def send_telegram(text):
    """Optional: push the same line to a Telegram chat."""
    if not (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID):
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(
            url,
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text},
            timeout=5,
        )
    except Exception as e:
        print(f"Telegram error: {e}")


def short_wallet(addr):
    return f"{addr[:4]}…{addr[-4:]}" if len(addr) > 8 else addr


def process_transaction(wallet, signature):
    """Decode one transaction and print a BUY/SELL line if it's a swap."""
    tx = get_transaction(signature)
    if not tx:
        return

    meta = tx.get("meta")
    if not meta or meta.get("err"):      # skip failed transactions
        return

    # aggregate token amounts for this wallet (handles multiple token accounts)
    pre_sum = {}
    for b in meta.get("preTokenBalances") or []:
        if b.get("owner") == wallet:
            mint = b["mint"]
            amt = b["uiTokenAmount"]["uiAmount"] or 0.0
            pre_sum[mint] = pre_sum.get(mint, 0.0) + amt

    post_sum = {}
    for b in meta.get("postTokenBalances") or []:
        if b.get("owner") == wallet:
            mint = b["mint"]
            amt = b["uiTokenAmount"]["uiAmount"] or 0.0
            post_sum[mint] = post_sum.get(mint, 0.0) + amt

    # find the token with the biggest absolute change
    deltas = []
    for mint in set(pre_sum) | set(post_sum):
        delta = post_sum.get(mint, 0.0) - pre_sum.get(mint, 0.0)
        if abs(delta) > 0:
            deltas.append((mint, delta))

    if not deltas:
        return

    mint, delta = max(deltas, key=lambda x: abs(x[1]))
    direction = "BUY" if delta > 0 else "SELL"
    amount = abs(delta)

    info = get_token_info(mint)
    symbol = info["symbol"]
    price = info["priceUsd"]
    usd = amount * price if price is not None else None

    # format one line
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sw = short_wallet(wallet)
    amt_str = f"{amount:.4f}".rstrip("0").rstrip(".") if amount < 1 else f"{amount:.2f}"
    usd_str = f"${usd:.2f}" if usd is not None else "N/A"
    link = f"https://dexscreener.com/solana/{mint}"

    line = f"{ts} | {sw} | {direction} | {symbol} | {amt_str} | {usd_str} | {link}"
    print(line)
    send_telegram(line)


# --- main loop --------------------------------------------------------------
def main():
    wallets = load_wallets()
    if not wallets:
        print("No wallets found in wallets.txt")
        return

    # remember the last signature we've seen for each wallet
    last_sig = {w: None for w in wallets}

    # on startup, set the baseline to the latest signature (avoid printing old txs)
    for w in wallets:
        sigs = get_signatures(w, limit=1)
        if sigs:
            last_sig[w] = sigs[0]

    print(f"Watching {len(wallets)} wallet(s). Polling every {POLL_INTERVAL}s…")

    while True:
        try:
            for w in wallets:
                sigs = get_signatures(w, limit=5)   # small batch is enough
                if not sigs:
                    continue

                prev = last_sig[w]
                if prev is None:
                    last_sig[w] = sigs[0]
                    continue

                # signatures come newest‑first; find where the old one sits
                if prev in sigs:
                    idx = sigs.index(prev)
                    new_sigs = sigs[:idx]           # newer than prev
                else:
                    # prev scrolled out of the window – process the whole batch
                    new_sigs = sigs

                # process oldest‑first so the output is chronological
                for sig in reversed(new_sigs):
                    process_transaction(w, sig)
                    time.sleep(1)                   # be gentle with the RPC

                last_sig[w] = sigs[0]               # update to the latest

            time.sleep(POLL_INTERVAL)

        except KeyboardInterrupt:
            print("Stopped.")
            break
        except Exception as e:
            print(f"Main loop error: {e}")
            time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
