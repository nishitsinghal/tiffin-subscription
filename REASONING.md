# REASONING


## Understanding the Problem


The application manages monthly tiffin subscriptions and ensures that customers are billed only for the days they actually received service.


The design also supports the three assigned twists:

- T1 — Morning notifications

- T6 — Mid-cycle transfer

- T4 — Messy data import


## Data Model


Customer and Subscription are kept separate because a subscription can move from one customer to another.


`Ownership` stores:

- customer

- subscription

- start date

- end date


`Pause` stores actual pause date ranges instead of a simple counter.


`Notification` stores generated delivery notifications.


`SystemClock` provides a simulated current date for testing.


## Billing Logic


The monthly daily rate is:


```text

monthly plan price / days in month

```


Only days that are both owned by the customer and not paused are counted.


```text

bill = daily rate × served days

```


Because ownership is stored by date range, transferred subscriptions are automatically split between the old and new customer.


## T1 — Notifications


`POST /clock` changes the simulated date and runs the morning job.


A notification is created when:

- it is a weekday

- the customer owns a subscription

- the subscription is not paused


Existing notifications for the same customer and date are not duplicated.


## T6 — Transfer


A transfer keeps the same subscription.


The old ownership ends before the transfer date and a new ownership starts on the transfer date.


This preserves the plan price and pause history while allowing billing to be split correctly.


## T4 — Messy Import


The import API validates each record independently.


Phone numbers are normalized, multiple date formats are supported, and invalid records do not stop the whole import.


Records are returned as:

- imported

- deduped

- rejected


## Testing


The main tests performed were:

- registration and login

- customer subscription

- pause and resume

- pro-rated billing

- mid-cycle transfer

- weekday notifications

- weekend notification skipping

- duplicate notification prevention

- messy data import

- search

- sorting

- pagination


## Future Improvements


Possible future improvements include:

- real SMS/WhatsApp notifications

- customer self-service portal

- CSV import

- detailed billing history

- delivery route planning