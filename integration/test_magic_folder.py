from os.path import join, exists

# see "conftest.py" for the fixtures (e.g. "magic_folder")

def test_eliot_logs_are_written(alice, bob, temp_dir):
    # The integration test configuration arranges for this logging
    # configuration.  Verify it actually does what we want.
    #
    # The alice and bob arguments looks unused but they actually tell pytest
    # to set up all the magic-folder stuff.  The assertions here are about
    # side-effects of that setup.
    assert exists(join(temp_dir, "alice", "logs", "eliot.json"))
    assert exists(join(temp_dir, "bob", "logs", "eliot.json"))
