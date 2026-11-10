"""Rails adapter. Controller filters (before_action :authenticate..., CanCanCan
load_and_authorize_resource, Pundit verify_authorized, parents and concerns) are resolved by the
route inventory into auth_detected; this adapter adds what that cannot say: the controller is not
in the scanned files (unresolved) and authentication actions (public by design)."""
from __future__ import annotations

from .core import _LOCAL, PUBLIC, UNRESOLVED, AuthVerdict, Resolver, public_action


def adapter(ctx: Resolver, route: dict) -> AuthVerdict:
    extra = ctx.route_extra(route)
    controller = extra.get("controller", "")
    if public_action(route, extra):
        return AuthVerdict(PUBLIC, "authentication action (public by design)")
    if controller and not extra.get("found"):
        return AuthVerdict(UNRESOLVED, "app/controllers/%s_controller.rb (its before_action "
                                       "filters) is not in the scanned files" % controller)
    if extra.get("missing_parent"):
        return AuthVerdict(UNRESOLVED, "%s_controller.rb inherits from a controller that is not "
                                       "in the scanned files (filters declared there are not "
                                       "seen)" % controller)
    return _LOCAL
