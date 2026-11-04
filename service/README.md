# MASTER on-demand update service

This service runs the existing validated Python pipeline on a small VPS when the Android app requests an update. It does **not** use GitHub Actions minutes. It still publishes the existing manifest and delta files to this repository, so the APK's current download/verification/import path can remain in place.

## Security and prerequisites

- Deploy on a VPS with Docker Compose and HTTPS reverse proxy (Caddy/Nginx).
- Create a GitHub fine-grained token limited to this repository with **Contents: Read and write**. Keep it only on the VPS; never put it in the APK.
- Generate a separate long random `UPDATE_TRIGGER_TOKEN`. The APK trigger credential is extractable from an APK, so protect the endpoint with HTTPS, global lock, 3-minute cooldown, and ideally a reverse-proxy rate limit. Do not expose port 8080 publicly.
- Keep `/data` persistent. It stores the checkout and update lock/state.
- The service serializes runs, rechecks the last seven days, validates gzip hashes and row counts, and only then pushes the manifest and packages. A failed run does not publish a partial package.

## Deploy

From this repository root on the VPS:

1. Copy `service/.env.example` to `service/.env` and fill both secrets.
2. Run `docker compose --env-file service/.env -f service/compose.yml up -d --build`.
3. Put an HTTPS reverse proxy in front of `127.0.0.1:8080`.
4. Verify `GET /health` through HTTPS.
5. Test `POST /v1/update` with `Authorization: Bearer <UPDATE_TRIGGER_TOKEN>`.

Example:

```sh
curl -X POST https://YOUR-DOMAIN/v1/update \
  -H "Authorization: Bearer YOUR_UPDATE_TRIGGER_TOKEN"
```

The Android app must call this endpoint before refreshing the manifest. It should then poll the manifest/current version until it changes or the service reports already up to date. Do not merge this service into the APK until the VPS URL and request flow have been tested.

## Important current limitation

This is the server-side deployment scaffold only. It does not deploy itself, create a VPS, configure DNS/TLS, or modify the Android app. The existing GitHub Actions schedule should remain enabled until the service has been deployed and a real end-to-end APK update has passed.
