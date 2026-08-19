from availability_service.app.probe_airbnb import run_probe_airbnb


def test_probe_refuses_without_confirm_live():
    try:
        run_probe_airbnb("A_20260810_003", confirm_live=False)
        assert False, "expected SystemExit"
    except SystemExit as exc:
        assert "confirm-live" in str(exc).lower()


def test_probe_refuses_empty_object_id():
    try:
        run_probe_airbnb("", confirm_live=True)
        assert False, "expected SystemExit"
    except SystemExit as exc:
        assert "object-id" in str(exc).lower()
