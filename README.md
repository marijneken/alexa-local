# Alexa Local

A self-hosted bridge between Home Assistant and your own Alexa Smart Home skill. No Nabu Casa subscription, no Matter bridge: Alexa talks to Home Assistant's built-in Alexa code through your own skill and a tiny AWS Lambda relay.

## Features

- **UI-only configuration.** Expose entities (or whole devices) by giving them a label (default `alexa`). Removing the label removes the device from Alexa.
- **State reporting.** Changes made in HA show up in the Alexa app straight away, and added/removed/changed devices are pushed to Alexa automatically (no "discover devices" needed).
- **Inverted covers.** Add an *Inverted cover* to give Alexa a reversed copy of a cover (position, open/close and tilt).
- **Mapped controls.** Add an extra Alexa control to any exposed device that drives a `select`/`input_select` (each option mapped to a value, option names usable as presets) or a `number`/`input_number` (linear mapping, swap min/max to invert). Example: "Alexa, set the fan swing to 50 percent".
- A diagnostic sensor lists what is currently exposed.

## How it works

```
Echo -> Alexa cloud -> your Smart Home skill -> AWS Lambda (lambda/lambda_function.py)
     -> https://your-ha/api/alexa_local/smart_home -> Home Assistant
```

Account linking uses Home Assistant's own OAuth login. State reports go from Home Assistant directly to Amazon's event gateway.

## Setup

1. **Home Assistant** must be reachable over HTTPS on port 443 with a trusted certificate.
2. **Install** via HACS (custom repository, type *Integration*) and restart Home Assistant.
3. **Lambda.** Create a Python Lambda in the AWS region that matches your Alexa account (North America: `us-east-1`, Europe/India: `eu-west-1`, Far East: `us-west-2`) with the code in `lambda/lambda_function.py` and the environment variable `BASE_URL=https://your-ha`.
4. **Skill.** In the Alexa developer console create a *Smart Home* skill (payload v3, *Provision your own*):
   - Default endpoint: the Lambda ARN. Then add an *Alexa Smart Home* trigger with the skill ID to the Lambda.
   - Account linking: Auth Code Grant, authorization URI `https://your-ha/auth/authorize`, token URI `https://your-ha/auth/token`, client ID `https://pitangui.amazon.com/` (NA), `https://layla.amazon.com/` (EU) or `https://alexa.amazon.co.jp/` (FE), any client secret, authentication scheme *Credentials in request body*, scope `smart_home`.
   - Permissions: enable *Send Alexa Events* and note the client ID and secret.
5. **Integration.** Add *Alexa Local* in Settings -> Devices & Services and enter the client ID and secret from step 4.
6. **Link.** In the Alexa app open Skills -> Your Skills -> Dev -> your skill -> Enable, and log in to Home Assistant.

## Development

```
pip install pytest-homeassistant-custom-component
pytest
```
