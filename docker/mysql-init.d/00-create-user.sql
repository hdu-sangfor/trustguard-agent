-- Ensure trustguard user exists for localhost and any host.
-- when the volume was created without MYSQL_USER/MYSQL_PASSWORD or when connecting from host to Docker MySQL.
CREATE USER IF NOT EXISTS 'trustguard'@'%' IDENTIFIED BY 'trustguard';
CREATE USER IF NOT EXISTS 'trustguard'@'localhost' IDENTIFIED BY 'trustguard';
GRANT ALL PRIVILEGES ON trustguard_agent.* TO 'trustguard'@'%';
GRANT ALL PRIVILEGES ON trustguard_agent.* TO 'trustguard'@'localhost';
FLUSH PRIVILEGES;
