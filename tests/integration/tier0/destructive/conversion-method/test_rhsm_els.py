from conftest import TEST_VARS


def test_rhsm_with_els_system_conversion(convert2rhel, shell, fixture_subman):
    """
    Verify that Convert2RHEL is working properly when ELS repositories are used during the conversion.
    Verify that the correct repositories are enabled after the conversion (in one of the check-after-conversion tests).
    """

    # Mark the system so the check for the enabled repos after the conversion handles this special case
    shell("touch /els_repos_used")

    with convert2rhel(
        "-y --username {} --password {} --debug --els".format(
            TEST_VARS["RHSM_SCA_USERNAME"],
            TEST_VARS["RHSM_SCA_PASSWORD"],
        )
    ) as c2r:
        c2r.expect_exact("Enabling RHEL repositories:")
        c2r.expect_exact("rhel-7-server-els-rpms")
        c2r.expect_exact("Conversion successful!")
    assert c2r.exitstatus == 0
