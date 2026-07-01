from flask import flash, redirect, render_template, url_for

from services import queries
from auth import security


def register_routes(app):
    @app.get("/dashboard")
    @security.login_required
    def dashboard():
        categories = queries.get_categories()
        user = queries.get_current_user()
        return render_template(
            "dashboard.html", categories=categories, user=user, active_page="dashboard"
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
