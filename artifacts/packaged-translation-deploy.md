# Package the local translation module

Apply on `6e80d33fa77b3b964478bd95ef3bf0c92e0907af`. Do not reorder earlier patches. Do not download weights again and do not change the translation model.

Two production entries failed after that build:

- `llm-pi-ai` evaluated `!!js` with a bare `require`. The ESM loader (`vendor/loader/lib/index.js` eval / interpolate) throws `require is not defined`.
- `youdub-subtitles` loads `createRequire(import.meta.url)('./local-translation.cjs')` from `dist/host.mjs`. `prepare-source-development` copied only `host.mjs` and `client.js`, so the runtime plugin did not contain `local-translation.cjs`.

The loader expression now uses `process.getBuiltinModule('node:module').createRequire` and reads the file prepare places at `youdub-plugin/dist/local-translation.cjs`. The official build also copies that file next to `apps/harness/dist/host.mjs`. No manual copy.

```sh
git apply --check artifacts/youdub-packaged-translation-since-6e80d33.patch
git apply artifacts/youdub-packaged-translation-since-6e80d33.patch
npm --prefix apps/harness run build
npm --prefix apps/harness start
```

Restart only `youdub-host`, and only when no task is running. Keep the existing origin, launch token, and the four translation variables. Do not touch 8888 or 8889.

Startup is acceptable when the log has no `require is not defined`, no `MODULE_NOT_FOUND` for `local-translation.cjs`, and no `did not activate`. An anonymous product API call is 401, the token exchange is 303, and an authenticated `GET /api/v2/tasks` is 200. The seven succeeded tasks stay in place.
