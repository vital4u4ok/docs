# Meridian Bank API docs

Mintlify documentation site for a fictional banking API. The OpenAPI description behind it was
written to exercise as much of the specification as one document reasonably can: every security
scheme type, every parameter serialization style, deprecation at every level the specification
allows, and the parts of JSON Schema 2020-12 that matter in practice.

Nothing here is a real service. Hostnames, IBANs and card numbers are fictional.

## The two OpenAPI descriptions

| Path | What it is |
| --- | --- |
| `openapi.yaml` | The source of truth. OpenAPI **3.2.0**. |
| `openapi-3.1.yaml` | Generated from the above. What the API reference renders. |
| `scripts/generate-openapi-3.1.py` | The 3.2 to 3.1 downgrade, and the `docs.json` nav refresh. |

Mintlify renders OpenAPI 3.0 and 3.1 only. Edit the 3.2 file, then regenerate from the
repository root:

```bash
python3 scripts/generate-openapi-3.1.py
```

That rewrites `openapi-3.1.yaml`, refreshes the endpoint list in `docs.json`, and verifies that
the YAML it wrote reloads to the same document it built.

`openapi.yaml` is listed in `.mintignore`. Mintlify validates every OpenAPI file it finds under
the directory holding `docs.json`, whether or not a page references it, and it rejects 3.2.

## Validation

```bash
mint validate        # build and navigation
mint broken-links    # internal links
```

`openapi.yaml` validates against the official OpenAPI 3.2 meta-schema. `openapi-3.1.yaml` passes
`redocly lint` with no errors.

## AI-assisted writing

Set up your AI coding tool to work with Mintlify:

```bash
npx skills add https://mintlify.com/docs
```

This command installs Mintlify's documentation skill for your configured AI tools like Claude Code, Cursor, Windsurf, and others. The skill includes component reference, writing standards, and workflow guidance.

See the [AI tools guides](/ai-tools) for tool-specific setup.

## Development

Install the [Mintlify CLI](https://www.npmjs.com/package/mint) to preview your documentation changes locally. To install, use the following command:

```
npm i -g mint
```

Run the following command at the root of your documentation, where your `docs.json` is located:

```
mint dev
```

View your local preview at `http://localhost:3000`.

## Publishing changes

Install our GitHub app from your [dashboard](https://dashboard.mintlify.com/settings/organization/github-app) to propagate changes from your repo to your deployment. Changes are deployed to production automatically after pushing to the default branch.

## Need help?

### Troubleshooting

- If your dev environment isn't running: Run `mint update` to ensure you have the most recent version of the CLI.
- If a page loads as a 404: Make sure you are running in a folder with a valid `docs.json`.

### Resources
- [Mintlify documentation](https://mintlify.com/docs)
