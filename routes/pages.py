from flask import flash, make_response, redirect, render_template, url_for

import db
from services import payloads, queries
from auth import security


def register_routes(app):
    @app.get("/dashboard")
    @security.login_required
    def dashboard():
        categories = queries.get_categories()
        user = queries.get_current_user()
        current_ts = db.now_ts()
        recent_sessions, recent_total = queries.recent_sessions_for_user(
            user["id"], limit=10, offset=0
        )
        trend_rows = queries.user_activity_grid(user["id"], current_ts, days=14)
        initial_data = {
            "status": payloads.build_status_payload(user["id"], current_ts, current_ts),
            "digest": payloads.build_weekly_digest(user["id"], current_ts, limit=5),
            "recent": {
                "sessions": recent_sessions,
                "total": recent_total,
                "has_more": len(recent_sessions) < recent_total,
            },
            "trend": {
                "days": [row["date"] for row in trend_rows],
                "seconds": [row["seconds"] for row in trend_rows],
            },
        }
        return render_template(
            "dashboard.html",
            categories=categories,
            user=user,
            active_page="dashboard",
            initial_data=initial_data,
        )

    @app.get("/weekly-leaderboard")
    @security.login_required
    def weekly_leaderboard():
        user = queries.get_current_user()
        return render_template(
            "weekly_leaderboard.html", user=user, active_page="weekly_leaderboard"
        )

    @app.get("/all-time-stats")
    @security.login_required
    def all_time_stats():
        user = queries.get_current_user()
        return render_template(
            "all_time_stats.html", user=user, active_page="all_time_stats"
        )

    @app.get("/users/<int:user_id>")
    @security.login_required
    def user_profile(user_id: int):
        target_user = queries.get_user_by_id(user_id)
        if not target_user:
            flash("User not found.", "error")
            return redirect(url_for("dashboard"))
        current_user = queries.get_current_user()
        categories = queries.get_categories()
        return render_template(
            "user.html",
            user=current_user,
            target_user=target_user,
            can_delete_sessions=current_user["id"] == target_user["id"],
            categories=categories,
            active_page="user_profile",
        )

    @app.route("/service-worker.js")
    def service_worker():
        # Rendered as a template so the SW's cache name is the server-computed
        # static content hash (app.config["STATIC_VERSION"]): a deploy that
        # changes any asset yields a new SW, which installs with new versioned
        # asset URLs. Served uncacheable so browsers pick up the new SW on
        # their next navigation instead of after the old header expires.
        response = make_response(
            render_template(
                "service-worker.js", cache_version=app.config["STATIC_VERSION"]
            )
        )
        response.headers["Content-Type"] = "application/javascript"
        response.headers["Cache-Control"] = "no-cache"
        return response
