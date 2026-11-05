# Installation

Requires Home Assistant 2026.11 or later for conditional configuration fields.
This minimum applies to every installation method.

## Home Assistant Marketplace

Use the Marketplace badge in the [README](../README.md) to add this repository
as a custom integration, install **Microsoft Teams**, and restart Home
Assistant. The badge does not indicate that the integration is in the public
catalog.

## HACS

1. In Home Assistant, open **HACS** and add
   `https://github.com/pschmitt/homeassistant-msteams` as a custom repository.
2. Choose **Integration** as the category and add the repository.
3. Install **Microsoft Teams** and restart Home Assistant.

## Manual installation

Copy the `custom_components/msteams` directory from this repository into the
Home Assistant configuration directory. The resulting path must be:

```text
<home-assistant-config>/custom_components/msteams/
```

The repository root is not the integration directory; copy the nested
`custom_components/msteams` folder. Restart Home Assistant after copying it.

## Local checkout or submodule

Keep the repository at
`<home-assistant-config>/custom_components-local/homeassistant-msteams`, then
link the nested integration directory from the configuration directory:

```sh
ln -s ../custom_components-local/homeassistant-msteams/custom_components/msteams \
  custom_components/msteams
```

Restart Home Assistant after updating the checkout. The symlink target must be
accessible inside the Home Assistant runtime, not only on the host.

## Add the integration

Open **Settings** → **Devices & Services** → **Add Integration**, search for
**Microsoft Teams**, and follow the form. For browser sign-in, configure the
browser-daemon integration first. See [Configuration](configuration.md) and
[Authentication](authentication.md).
