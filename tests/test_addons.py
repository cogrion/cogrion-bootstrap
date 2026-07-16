import yaml
from cogrion_bootstrap.addons import make_external_dns

# --- make_external_dns ---


def test_external_dns_sets_webhook_read_timeout():
    addon = make_external_dns("https://cplane-api.example.com")
    values = yaml.safe_load(addon.values_yaml)

    assert values["extraArgs"]["webhook-provider-read-timeout"] == "30s"


def test_external_dns_sets_reconcile_interval():
    addon = make_external_dns("https://cplane-api.example.com")
    values = yaml.safe_load(addon.values_yaml)

    assert values["interval"] == "5m"
