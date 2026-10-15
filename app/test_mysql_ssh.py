import getpass
import paramiko
import pymysql
import socket
import threading

SSH_HOST = "remote.io.vn"
SSH_PORT = 11572
SSH_USER = "root"

DB_HOST = "127.0.0.1"
DB_PORT = 3306
DB_USER = "stockmate_user"
DB_NAME = "stockmate"


def main():
    ssh_password = getpass.getpass("Nhap mat khau SSH: ")
    db_password = getpass.getpass("Nhap mat khau MariaDB: ")

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    try:
        print("Dang ket noi SSH...")

        ssh.connect(
            hostname=SSH_HOST,
            port=SSH_PORT,
            username=SSH_USER,
            password=ssh_password,
            timeout=15,
            banner_timeout=15,
            auth_timeout=15,
        )

        transport = ssh.get_transport()
        transport.set_keepalive(30)

        # Mo cong local ngau nhien
        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen(5)
        local_port = server.getsockname()[1]

        def forward():
            while True:
                try:
                    client_socket, _ = server.accept()
                except OSError:
                    break

                def handle(client):
                    channel = None
                    try:
                        channel = transport.open_channel(
                            "direct-tcpip",
                            (DB_HOST, DB_PORT),
                            client.getpeername(),
                        )

                        if channel is None:
                            return

                        while True:
                            ready = __import__("select").select(
                                [client, channel], [], []
                            )[0]

                            if client in ready:
                                data = client.recv(4096)
                                if not data:
                                    break
                                channel.sendall(data)

                            if channel in ready:
                                data = channel.recv(4096)
                                if not data:
                                    break
                                client.sendall(data)
                    except Exception as exc:
                        print("Loi tunnel:", exc)
                    finally:
                        if channel:
                            channel.close()
                        client.close()

                threading.Thread(
                    target=handle,
                    args=(client_socket,),
                    daemon=True,
                ).start()

        threading.Thread(target=forward, daemon=True).start()

        print(f"SSH tunnel dang chay tai 127.0.0.1:{local_port}")
        print("Dang ket noi MariaDB...")

        conn = pymysql.connect(
            host="127.0.0.1",
            port=local_port,
            user=DB_USER,
            password=db_password,
            database=DB_NAME,
            connect_timeout=10,
            charset="utf8mb4",
        )

        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT VERSION(), DATABASE(), CURRENT_USER()"
                )
                version, database, user = cursor.fetchone()

                print("\nKET NOI MARIADB THANH CONG!")
                print("Version :", version)
                print("Database:", database)
                print("User    :", user)

                cursor.execute("SHOW TABLES")
                tables = cursor.fetchall()

                print(f"So bang: {len(tables)}")
                for (table,) in tables[:20]:
                    print(" -", table)
        finally:
            conn.close()

    except Exception as exc:
        print(f"\nKET NOI THAT BAI: {type(exc).__name__}: {exc}")

    finally:
        ssh.close()
        print("Da dong SSH.")


if __name__ == "__main__":
    main()
