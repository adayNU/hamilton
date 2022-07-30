import operator

import pytest

from hamilton.dev_utils.deprecation import Version, deprecated, DeprecationError


@pytest.mark.parametrize(
    'version_1, version_2, op',
    [
        (Version(0, 1, 2), Version(0, 1, 2), operator.eq),
        (Version(0, 1, 2), Version(0, 1, 3), operator.lt),
        (Version(0, 1, 2), Version(0, 1, 1), operator.gt),
        (Version(1, 9, 0), Version(2, 0, 0), operator.lt),
        (Version(2, 0, 0), Version(1, 9, 0), operator.gt),
    ]
)
def test_version_compare(version_1, version_2, op):
    assert op(version_1, version_2)


@pytest.mark.parametrize(
    'kwargs',
    [
        dict(warn_starting=(0, 0, 0), fail_starting=(0, 0, 1), use_this=None,
             explanation='something', migration_guide='https://github.com/stitchfix/hamilton'),
        dict(warn_starting=(0, 0, 0), fail_starting=(0, 0, 1), use_this=test_version_compare,
             explanation='something', migration_guide=None),
        dict(warn_starting=(0, 0, 0), fail_starting=(1, 0, 0), use_this=test_version_compare,
             explanation='something', migration_guide=None)
    ]
)
def test_validate_deprecated_decorator_params_happy(kwargs):
    deprecated(**kwargs)


@pytest.mark.parametrize(
    'kwargs',
    [
        dict(warn_starting=(1, 0, 0), fail_starting=(0, 0, 1), use_this=None,
             explanation='something', migration_guide='https://github.com/stitchfix/hamilton'),
        dict(warn_starting=(0, 0, 0), fail_starting=(0, 0, 1), use_this=None,
             explanation='something', migration_guide=None),
        dict(warn_starting=(0, 0, 0), fail_starting=(1, 0, 0), use_this=None,
             explanation='something', migration_guide=None)
    ]
)
def test_validate_deprecated_decorator_params_sad(kwargs):
    with pytest.raises(ValueError):
        deprecated(**kwargs)


def test_call_function_not_deprecated_yet():
    warned = False

    def warn(s):
        nonlocal warned
        warned = True

    def replacement_function() -> bool:
        return True

    @deprecated(
        warn_starting=(0, 5, 0),
        fail_starting=(1, 0, 0),
        use_this=replacement_function,
        explanation='True is the new False',
        migration_guide='https://github.com/stitchfix/hamilton',
        current_version=(0, 0, 0),
        warn_action=warn
    )
    def deprecated_function() -> bool:
        return False

    assert not warned


def test_call_function_soon_to_be_deprecated():
    warned = False

    def warn(s):
        nonlocal warned
        warned = True

    def replacement_function() -> bool:
        return True

    @deprecated(
        warn_starting=(0, 5, 0),
        fail_starting=(1, 0, 0),
        use_this=replacement_function,
        explanation='True is the new False',
        migration_guide='https://github.com/stitchfix/hamilton',
        current_version=(0, 6, 0),
        warn_action=warn
    )
    def deprecated_function() -> bool:
        return False

    assert warned


def test_call_function_already_deprecated():
    def replacement_function() -> bool:
        return True

    with pytest.raises(DeprecationError):
        @deprecated(
            warn_starting=(0, 5, 0),
            fail_starting=(1, 0, 0),
            use_this=replacement_function,
            explanation='True is the new False',
            migration_guide='https://github.com/stitchfix/hamilton',
            current_version=(1, 1, 0),
        )
        def deprecated_function() -> bool:
            return False