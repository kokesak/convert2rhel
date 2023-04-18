import pytest

from envparse import env


@pytest.mark.rhsm_conversion
def test_run_conversion(convert2rhel):
    username = "your_ussername"
    password = "your_password"
    with convert2rhel("-y --no-rpm-va --username {} --password {} --debug".format(username, password)) as c2r:
        c2r.expect("Conversion successful!")
    assert c2r.exitstatus == 0
