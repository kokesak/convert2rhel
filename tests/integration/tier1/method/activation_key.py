import pytest

from envparse import env


@pytest.mark.activation_key_conversion
def test_activation_key_conversion(convert2rhel):
    key = "your_key"
    org = "your_org"
    with convert2rhel("-y --no-rpm-va -k {} -o {} --debug".format(key, org)) as c2r:
        c2r.expect("Conversion successful!")
    assert c2r.exitstatus == 0
