# Deploy Instructions

## Using Docker Compose

### Prerequisites
- Docker
- Docker Compose

### Steps

1. **Build and start the application:**
   ```bash
   docker-compose up -d
   ```

2. **View logs:**
   ```bash
   docker-compose logs -f
   ```

3. **Stop the application:**
   ```bash
   docker-compose down
   ```

4. **Rebuild after code changes:**
   ```bash
   docker-compose up -d --build
   ```

### Access the Application
- Open browser: http://localhost:8080

### Database Persistence
The database file `kok_data.db` is mounted as a volume, so data persists even when containers are stopped.

## Production Deployment

For production deployment, consider:

1. **Use environment variables** for sensitive configuration
2. **Set up reverse proxy** (nginx) for SSL/TLS
3. **Use production WSGI server** (gunicorn) instead of Flask development server
4. **Set up monitoring** and logging
5. **Backup database** regularly

### Example with Gunicorn

Update Dockerfile CMD:
```dockerfile
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "4", "app:app"]
```

Add gunicorn to requirements.txt:
```
gunicorn==21.2.0
```

