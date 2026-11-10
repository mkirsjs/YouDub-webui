# Schema description strings

Apply on `66085fa55dcb8fb99e44c8357ad89487bc1e02ba`. This only changes the two synthesize field descriptions that Ajv rejected. Phrase grouping, speaker scope, Index, upload progress, and auth stay as deployed.

`groupCues.description` and `speakerScope.description` are strings. Ajv draft 2020 rejects an object there, which made `POST /api/v2/workflow-documents/validate` and upload return 500 before a task existed.

```sh
git apply --check artifacts/youdub-schema-description-since-66085fa.patch
git apply artifacts/youdub-schema-description-since-66085fa.patch
npm --prefix apps/harness run build
```

Restart `youdub-host` only when no task is running. Then validate the built-in「视频配音」graph. Do not create a task, do not run a short video, and do not change the database. Anonymous product API stays 401. A successful validate response is 200 with `valid: true`.
