# Deploy CCFoundry Dev Board

## Deploy And Open Dev Board

Your CCFoundry Agent Kit checkout is ready in Google Cloud Shell.

Select the Google Cloud project you want to use for this demo. Billing is
required because the deploy script creates a Compute Engine VM.

<walkthrough-project-setup billing="true"></walkthrough-project-setup>

If you do not have a suitable project yet, use the "Create a new project" link
above. You can also type `new` in the Terminal project prompt.

Then click the button on the code block below to deploy CCFoundry Agent Dev
Board.

```sh
./deploy.sh && cat .ccfoundry-dev-board-url
```

When deployment finishes, the Terminal prints the real Web UI URL as the final
line. Click that final terminal URL to open CCFoundry Agent Dev Board.

<walkthrough-conclusion-trophy></walkthrough-conclusion-trophy>
