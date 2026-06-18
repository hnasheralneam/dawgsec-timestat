from flask import jsonify, request, session

import config
import db
import parsing
import queries
import security


def register_routes(app):
    @app.get("/api/leaderboard")
    @security.login_required
    def api_leaderboard():
        current_ts = db.now_ts()
        return jsonify(
            {"leaderboard": queries.leaderboard_rows(current_ts), "server_ts": current_ts}
        )

    @app.get("/api/leaderboard/weekly")
    @security.login_required
    def api_weekly_leaderboard():
        current_ts = db.now_ts()
        since_ts = current_ts - config.WEEK_SECONDS
        limit_raw = request.args.get("limit")
        limit = None
        if limit_raw is not None:
            try:
                parsed_limit = int(limit_raw)
            except ValueError:
                return jsonify({"error": "limit must be an integer"}), 400
            if parsed_limit < 1:
                return jsonify({"error": "limit must be at least 1"}), 400
            limit = parsed_limit

        rows = queries.leaderboard_rows(current_ts, since_ts=since_ts)
        if limit is not None:
            rows = rows[:limit]
        return jsonify({"leaderboard": rows, "server_ts": current_ts, "since_ts": since_ts})

    @app.get("/api/stats")
    @security.login_required
    def api_stats():
        current_ts = db.now_ts()
        user_id = int(session["user_id"])
        my_rows = queries.category_rows_for_user(user_id, current_ts)
        team_rows = queries.category_rows_for_user(None, current_ts)
        since_ts = current_ts - config.WEEK_SECONDS
        my_week_rows = queries.category_rows_for_user(user_id, current_ts, since_ts=since_ts)
        team_week_rows = queries.category_rows_for_user(None, current_ts, since_ts=since_ts)

        return jsonify(
            {
                "my_categories": my_rows,
                "team_categories": team_rows,
                "my_categories_week": my_week_rows,
                "team_categories_week": team_week_rows,
                "since_ts": since_ts,
            }
        )

    @app.get("/api/recent-sessions")
    @security.login_required
    def api_recent_sessions():
        user_id = int(session["user_id"])
        parsed, error = parsing.parse_recent_sessions_query_args(request.args)
        if error:
            return jsonify({"error": error}), 400

        sessions, total = queries.recent_sessions_for_user(
            user_id,
            limit=parsed["limit"],
            offset=parsed["offset"],
            query_text=parsed["query_text"],
            category=parsed["category"],
        )
        has_more = parsed["offset"] + len(sessions) < total
        return jsonify(
            {
                "sessions": sessions,
                "limit": parsed["limit"],
                "offset": parsed["offset"],
                "total": total,
                "has_more": has_more,
            }
        )

    @app.get("/api/users/<int:user_id>/stats")
    @security.login_required
    def api_user_stats(user_id: int):
        target_user = queries.get_user_by_id(user_id)
        if not target_user:
            return jsonify({"error": "User not found"}), 404
        rows = queries.category_rows_for_user(user_id, db.now_ts())
        return jsonify(
            {
                "user": {"id": target_user["id"], "username": target_user["username"]},
                "categories": rows,
            }
        )

    @app.get("/api/users/<int:user_id>/recent-sessions")
    @security.login_required
    def api_user_recent_sessions(user_id: int):
        target_user = queries.get_user_by_id(user_id)
        if not target_user:
            return jsonify({"error": "User not found"}), 404

        parsed, error = parsing.parse_recent_sessions_query_args(request.args)
        if error:
            return jsonify({"error": error}), 400

        sessions, total = queries.recent_sessions_for_user(
            user_id,
            limit=parsed["limit"],
            offset=parsed["offset"],
            query_text=parsed["query_text"],
            category=parsed["category"],
        )
        has_more = parsed["offset"] + len(sessions) < total
        return jsonify(
            {
                "user": {"id": target_user["id"], "username": target_user["username"]},
                "sessions": sessions,
                "limit": parsed["limit"],
                "offset": parsed["offset"],
                "total": total,
                "has_more": has_more,
            }
        )

    @app.get("/api/users/<int:user_id>/activity-grid")
    @security.login_required
    def api_user_activity_grid(user_id: int):
        target_user = queries.get_user_by_id(user_id)
        if not target_user:
            return jsonify({"error": "User not found"}), 404

        days_raw = request.args.get("days")
        days = 140
        if days_raw is not None:
            try:
                days = int(days_raw)
            except ValueError:
                return jsonify({"error": "days must be an integer"}), 400
            if days < 1 or days > 366:
                return jsonify({"error": "days must be between 1 and 366"}), 400

        grid_rows = queries.user_activity_grid(user_id, db.now_ts(), days=days)
        max_seconds = max((row["seconds"] for row in grid_rows), default=0)
        return jsonify(
            {
                "user": {"id": target_user["id"], "username": target_user["username"]},
                "days": grid_rows,
                "max_seconds": max_seconds,
            }
        )
