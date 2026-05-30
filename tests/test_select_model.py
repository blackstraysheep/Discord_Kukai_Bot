from bot.models.select import SelectComment


def test_select_comment_uses_select_id_column_name():
    assert "select_id" in SelectComment.__table__.c
    assert "vote_id" not in SelectComment.__table__.c
