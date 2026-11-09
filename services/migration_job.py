"""Wait for PostgreSQL before applying the release's transactional migration."""
from .wait import wait_for
from .migrate import main

if __name__ == '__main__':
    wait_for('ADMIN_DATABASE_URL')
    main()
