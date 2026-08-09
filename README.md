# Railway Coinbase Paper Bot

This bot runs continuously on Railway even when your browser is closed. It uses live public Coinbase pricing and simulated money only.

## Deployment

1. Unzip this package.
2. Create a GitHub repository and upload all files from the unzipped folder.
3. In Railway choose **New Project → Deploy from GitHub repo**.
4. Select the repository.
5. Add a Railway volume mounted at `/data`.
6. Add the variables from `.env.example` in Railway Variables.
7. Keep `AUTO_TRADING=false` initially.
8. Generate a public domain under Railway Networking.
9. Open the site and test Paper Buy and Paper Sell.
10. Change `AUTO_TRADING=true` when ready for unattended paper testing.

The volume is required because the paper ledger is stored at `/data/state.json`.

This is paper trading, not a profit guarantee or real-money execution system.
