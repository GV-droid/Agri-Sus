# Deploy Agri Sus on Google Cloud

<walkthrough-tutorial-duration duration="30"></walkthrough-tutorial-duration>

## Welcome

This guided tour deploys **Agri Sus** — the all-India AI policy simulator for crop reallocation — to your own
Google Cloud project. You only copy commands into the terminal below; a script does the rest.

It uses Cloud Run, Cloud Build, Artifact Registry, Vertex AI (Gemini with Google Search grounding), BigQuery,
BigQuery ML (boosted tree + linear, registered in the Vertex AI Model Registry) and, optionally, Google Earth Engine.

You need a project with **billing turned on** and the **Owner** role on it (true if you created it).

Click **Start**.

## Choose your project

<walkthrough-project-setup billing="true"></walkthrough-project-setup>

Select or create a project above, then set it for the terminal:

```sh
gcloud config set project <walkthrough-project-id/>
```

If a box asks to **Authorize Cloud Shell**, click **Authorize**. If the terminal says you are not signed in, run
`gcloud auth login --update-adc` and follow the link.

## Go to the project folder

The terminal should be in the `agrisus` folder (it contains `Dockerfile`, `backend`, `frontend`, `deploy`). Check:

```sh
ls Dockerfile deploy/deploy.sh
```

If you see "No such file", go to the folder first, for example `cd ~/agrisus`.

## Run the installer

```sh
bash deploy/deploy.sh
```

Answer the two questions:

* `Continue? [Y/n]` → press **Enter**
* `Run the Earth Engine pipeline now? [y/N]` → press **Enter** (No) — you can add it later

The six steps take about 20–30 minutes:

1. enable the Google Cloud APIs
2. create the `agri-sus-run` service account (least privilege)
3. load BigQuery and train the BigQuery ML yield models
4. Earth Engine (skipped)
5. build the container with Cloud Build into Artifact Registry
6. deploy to Cloud Run

## Open your site

At the end the script prints:

`Done! Agri Sus is live at: https://agri-sus-….run.app`

Open that link (the first load takes about a minute while the optimizer warms up), then:

* pick a **Crisis scenario** and click **Run optimizer**
* open the **💰 Budget & returns** and **🚢 Trade shocks** tabs
* ask the agent: *What if Indonesia stops selling palm oil to India?*
* click a district → **🌱 Next crop**

## Optional: Google Earth Engine

Register the project for Earth Engine once (free for non-commercial and research use):
<https://code.earthengine.google.com/register>

Then run (1–2 hours; if Cloud Shell disconnects just run it again — finished parts are skipped):

```sh
USE_EARTH_ENGINE=1 bash deploy/deploy.sh
```

## Save money between demos

Scale to zero when nobody is using the site (the first visit afterwards takes 1–2 minutes):

```sh
gcloud run services update agri-sus --region us-central1 --min-instances 0
```

Back to always-warm for demo day:

```sh
gcloud run services update agri-sus --region us-central1 --min-instances 1
```

Delete everything when you are done:

```sh
bash deploy/teardown.sh
```

## Done

<walkthrough-conclusion-trophy></walkthrough-conclusion-trophy>

Agri Sus is live on Google Cloud. Full guide, settings, costs and troubleshooting: `docs/DEPLOY.md`.
