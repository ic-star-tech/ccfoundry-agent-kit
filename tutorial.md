# Deploy CCFoundry Dev Board

## Start The Deployment

Your CCFoundry Agent Kit checkout is ready in Google Cloud Shell.

Click the button on the code block below to run the deploy script in Cloud Shell.
The script creates a Compute Engine VM from the public CCFoundry image, opens
ports `3000` and `8090`, and prints the Dev Board URL when it is ready.

```sh
bash deploy.sh
```

If Cloud Shell asks you to choose a Google Cloud project, select the project you
want to use for this demo and run the same command again.

## Open Dev Board

When deployment finishes, the terminal prints links like these:

```none
Web UI:     http://<external-ip>:3000
Health API: http://<external-ip>:8090/health
```

Open the Web UI link to start using CCFoundry Agent Dev Board.

<walkthrough-conclusion-trophy></walkthrough-conclusion-trophy>
