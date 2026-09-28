\getenv app_user APP_DB_USER
\getenv app_password APP_DB_PASSWORD
\getenv app_database POSTGRES_DB
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE', :'app_user', :'app_password') \gexec
GRANT CONNECT, TEMPORARY ON DATABASE :"app_database" TO :"app_user";
GRANT USAGE, CREATE ON SCHEMA public TO :"app_user";
