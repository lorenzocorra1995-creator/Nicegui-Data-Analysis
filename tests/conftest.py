import pytest

from demo_data import make_demo_df

pytest_plugins = ["nicegui.testing.user_plugin"]


@pytest.fixture
def df():
    return make_demo_df()
