from datetime import date

from pydantic import BaseModel


class MonthReport(BaseModel):
    by_category: dict[str, int]
    by_kind: dict[str, int]
    total_expense: int
    total_income: int


class CalendarItem(BaseModel):
    date: date
    expense: int
    income: int
