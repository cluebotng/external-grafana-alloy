# Grafana Alloy

This is a hack to get `alloy` into a container using `pack` so it can be deployed on Toolforge.

It is slightly less terrible than the previous solution of dumping the pre-compiled binary onto NFS then executing it in a container.

Once T401075 / T363027 is resolved, this can be replaced.

## Logic
* `setup.py` handles setting up the container image (downloading the binary)
* `entrypoint.py` handles setting up the runtime (config)

## Testing locally
```
$ pack build --builder heroku/builder:24 external-grafana-alloy
```

## Production configuration
Remote to write to:
```
$ toolforge envvars create ALLOY_REMOTE_URL 'https://prometheus-prod-39-prod-eu-north-0.grafana.net/api/prom/push'
```

Source:
```
$ toolforge envvars create ALLOY_SCRAPE_TARGETS '[{"host": "botng", "port": 8118, "interval": "1s", "timeout": "1s"}]' 
```
or
```
$ toolforge envvars create ALLOY_SCRAPE_TARGETS '[{"type": "discover"}]'
```

Optional (really mandatory unless you're running an open prom server...):
```
$ toolforge envvars create ALLOY_REMOTE_USERNAME 'my user'
$ toolforge envvars create ALLOY_REMOTE_PASSWORD 'very secret'
```
