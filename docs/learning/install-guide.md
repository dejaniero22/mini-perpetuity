# Learning Note: install guide

- **The first blocker was not code.** The plan assumed "a local PostgreSQL 18" and Rio does not have one, so nobody could run the system. Install steps are part of the product.
- **Primary sources, read as text.** The official PostgreSQL download pages, the Postgres.app site and the EDB installer downloads page were fetched into an empty folder and read as plain text (never executed). Needed hosts were added to the sandbox allow-list with Rio's approval; the vendor's Windows install page was still not reachable, so Windows wizard details stay [U].
- **Defaults carry security meaning.** Postgres.app runs as the Mac user with no password. That is convenient, and it means software running as that user can act as database superuser. This is the ADR-M6b limit again, now written next to the install step so it is not a surprise.
- **Version discipline.** A release candidate of PostgreSQL 19 exists. The guide says to choose 18, because 18 is what the tests ran on.
- **Not verified.** Nobody has followed the guide on a Mac or Windows machine yet.
