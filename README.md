# Банковская система

Учебный проект: многоуровневая банковская платформа на чистом Python.
Модели счетов, управление клиентами, система транзакций, безопасность и отчётность.

Задание состоит из 7 этапов. Готовы этапы 1-4.

```
   день 1   типы счетов, наследование, полиморфизм          готово
   день 2   валидация, исключения, инкапсуляция             готово
   день 3   Client, Bank, безопасность, отчёты              готово
   день 4   Transaction, очередь, процессор                 готово
   день 5   не начат
   день 6   не начат
   день 7   не начат
```

## Требования

```
   Python 3.12
   loguru 0.7.x   структурные логи
   pytest 9.x     только для тестов
   ruff 0.15.x    только для форматирования и линта
```

## Запуск

```bash
pip install -r requirements-dev.txt

python -m src.main          # демонстрационный сценарий
python -m pytest -q         # 379 тестов
ruff format . && ruff check .
```

## Структура

```
   src/
      enums.py          AccountStatus, Currency, ClientStatus,
                        TransactionType, TransactionStatus, TransactionPriority
      exceptions.py     7 доменных исключений
      money.py          проверка и приведение денежных величин
      logging_setup.py  настройка логов для точки входа
      main.py           демонстрационный сценарий за все дни
      models/           что банк моделирует
         accounts.py    AbstractAccount -> BankAccount -> 3 наследника
         client.py      Client
         bank.py        Bank - реестр клиентов и счетов
         exchange.py    CurrencyConverter
         transactions.py  Transaction
         queue.py       TransactionQueue
         processor.py   TransactionProcessor
      services/         что банк делает
         fraud.py       FraudJournal - журнал и ночное окно
         auth.py        AuthService - вход и блокировки
         analytics.py   BankAnalytics - отчёты
   tests/
      conftest.py       общие фикстуры
      test_accounts.py      70 тестов
      test_client.py        56
      test_bank.py          62
      test_transactions.py  47
      test_processor.py     45
      test_queue.py         20
      test_exchange.py      17
      test_money.py         20
      test_logging.py       13
      test_services.py      29
```

## Архитектура

Зависимости идут только сверху вниз. Счёт не знает про банк, клиент не хранит
объекты счетов — только их идентификаторы.

```
   enums / exceptions        словарь состояний и ошибок
          |
          v
   accounts.py               один счёт: положить, снять, рассказать о себе
          |
          v
   client.py                 владелец: ФИО, контакты, пароль, список id счетов
          |
          v
   bank.py                   реестр всех клиентов и счетов, аудит, поиск
          |
          v
   transactions / queue / processor    операции над счетами через банк
          |
          v
   main.py                   только вызовы, без логики
```

`Bank` хранит клиентов и счета и владеет жизненным циклом счёта. Всё остальное
вынесено в сервисы, каждый из которых проверяется без банка:

```
   Bank (реестр)
     |
     +-- FraudJournal    журнал подозрительных действий, запрет 00:00-05:00
     +-- AuthService     проверка пароля, блокировка после 3 попыток
     +-- BankAnalytics   общий баланс и рейтинг клиентов
```

Публичный API банка при этом не изменился: `bank.authenticate_client(...)`,
`bank.get_total_balance(...)` работают как раньше, просто делегируют.

## Счета

```
   AbstractAccount (ABC)          контракт: deposit, withdraw, get_account_info
        |
        +-- BankAccount           обычный счёт + все проверки и property
              |
              +-- SavingsAccount     неснижаемый остаток, месячные проценты
              +-- PremiumAccount     овердрафт, фиксированная комиссия, лимит снятия
              +-- InvestmentAccount  портфель (stocks/bonds/etf), прогноз роста
```

Статус счёта проверяется первым в любой операции — единым методом
`ensure_operational`. Неизвестный статус даёт явную ошибку, а не тихий пропуск.
`CLOSED` — финальное состояние: обратно в `ACTIVE` или `FROZEN` счёт не вернётся.

Правила снятия счёт сообщает о себе сам, без проверок типа снаружи:

```
                        withdrawal_fee   min_allowed_balance
   BankAccount               0                  0
   SavingsAccount            0                  min_balance
   PremiumAccount            fixed_fee          -overdraft_limit
   InvestmentAccount         0                  0
```

## Деньги

Все суммы — `Decimal`, ровно два знака после запятой.

```
   разрешено    int, Decimal
   запрещено    float, bool, Decimal NaN, Decimal Infinity, строки
```

`float` запрещён намеренно: `Decimal + float` бросает `TypeError`, поэтому
дешевле не пускать его на вход, чем ловить взрыв в середине операции.
Округление — `ROUND_HALF_UP` до копеек.

## Клиенты и безопасность

```
   возраст            не младше 18
   контакты           только email и phone, оба строки, с проверкой формата
   пароль             pbkdf2-hmac-sha256, случайная соль, 200 000 итераций
   сравнение пароля   hmac.compare_digest, устойчиво к атаке по времени
   вход               3 неверные попытки -> статус BLOCKED
   аудит              подозрительные действия пишутся в журнал банка
   ночь               операции запрещены с 00:00 до 05:00
```

Открытый пароль нигде не хранится. Время в банк передаётся через
`time_provider`, поэтому ночной запрет тестируется без ожидания.

## Транзакции

```
                     отмена
   PENDING ------------------> CANCELLED
      |
      | (отложенная)
   SCHEDULED --ждёт времени--> PENDING
      |
      v
   PROCESSING --успех--------> COMPLETED
      |
      +--------ошибка--------> FAILED (+ причина отказа)
```

`Transaction` хранит id, тип, сумму, валюту, комиссию, отправителя, получателя,
статус, причину отказа, число попыток и три отметки времени.

`TransactionQueue` — куча с ключом `(приоритет, порядковый номер)`: HIGH раньше
NORMAL раньше LOW, внутри приоритета — FIFO. Отложенные транзакции пропускаются
до наступления срока и не блокируют очередь. Отмена — ленивое удаление.

`TransactionProcessor` считает комиссию, конвертирует валюту, повторяет попытки
и ведёт журнал ошибок.

```
   комиссия      внутренние операции          0
                 внешний перевод              max(1% от суммы, 50 руб)

   конвертация   все курсы через рубль, нет курса -> CurrencyConversionError

   повторы       деловая ошибка   (заморожен, нет денег)  -> сразу FAILED
                 временная ошибка (внешний шлюз недоступен) -> до 3 попыток

   откат         если вторая половина перевода упала,
                 деньги возвращаются отправителю
```

Статусы обоих счетов проверяются до первого движения денег, поэтому перевод на
замороженный счёт не может списать сумму с отправителя.

## Правила переводов

```
   уход в минус     запрещён, кроме PremiumAccount с овердрафтом
   замороженный
   или закрытый     операции запрещены обеим сторонам
   внешний перевод  облагается комиссией
```

## Тесты

379 тестов, около секунды.

Хеширование пароля намеренно медленное, поэтому в тестах число итераций
снижается через `monkeypatch`. Отдельный тест сторожит настоящее значение:

```python
def test_hashing_cost_is_high_in_production(self, real_password_iterations):
    assert real_password_iterations >= 200_000
```

Отдельно закрыты тестами две исправленные ошибки:

```
   test_status_is_checked_before_min_balance
        замороженный накопительный счёт даёт статусную ошибку,
        а не InsufficientFundsError

   test_matches_exact_type_only
        поиск по account_type="bank" находит только BankAccount,
        а не всех его наследников

   test_failed_external_transfer_returns_account_fee_too
   test_failed_transfer_returns_account_fee_too
        откат возвращает столько, сколько реально списали:
        у премиум-счёта есть своя комиссия сверх суммы перевода
```


## Логирование

Модели не печатают ничего в консоль — они сообщают о событиях через `loguru`,
а точка входа решает, куда эти события писать.

```python
logger.info("withdrawal", account_id=..., amount=..., currency=..., balance=...)
```

```
2026-09-04 14:22:12 | INFO | withdrawal | {'account_id': '49cf87ef',
    'amount': Decimal('2000.00'), 'fee': Decimal('25.00'),
    'currency': <Currency.RUB: 'rub'>, 'balance': Decimal('-825.00')}
```

Событие — это поля, а не текст: по такому логу можно искать и считать.
`configure_logging()` из `src/logging_setup.py` вызывается только в `main.py`.
Тест `test_no_print_in_domain_models` сторожит, чтобы `print` не вернулся в модели.
