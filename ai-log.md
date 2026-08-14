- Generated the pulumi files.

- How do we handle the environment variables?
- Claude is suggesting to use SSM Parameter Store as a SecureString.

- Encounter error in pulumi up
- Pre-empting the next error: once that import resolves, config.py runs and calls _cfg.require("studentToken"). If it isn't set you'll get ConfigMissingError immediately. Set it now so you don't round-trip:
```
pulumi config set studentToken <your token>
pulumi config          # confirm both aws:region and studentToken
```

