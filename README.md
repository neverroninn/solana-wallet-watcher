# Solana Wallet Watcher

A tiny weekend script that watches a few Solana wallets and prints when they buy or sell tokens.

It uses the free public Solana RPC and DexScreener’s free API – no API keys, no accounts.

## What it does

- Reads wallet addresses from `wallets.txt` (one per line, `#` for comments).
- Polls every 30 seconds.
- For each new swap it prints one line:

  ```
  2025-03-27 14:32:10 | 4xJ8…kL2p | BUY | BONK | 1500.00 | $30.50 | https://dexscreener.com/solana/DezXAZ…
  ```

- Optional: if you set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` as environment variables, the same line is also sent to a Telegram chat.

## Install

```bash
pip install requests
```

## Run

1. Put your wallets in `wallets.txt`, one per line.
2. (Optional) export the Telegram variables.
3. Run:

```bash
python watch.py
```

That’s it. It’s a hobby script – it won’t win any latency awards. If a wallet makes more than a handful of transactions between polls it might miss some or reprint the odd old one. It also only looks at SPL‑token balance changes, so native SOL movements on their own won’t trigger a line.

## Example output

```
2025-03-27 14:32:10 | 4xJ8…kL2p | BUY | BONK | 1500.00 | $30.50 | https://dexscreener.com/solana/DezXAZ…
```