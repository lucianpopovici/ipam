from app import app

def test_login_redirection():
    app.config['TESTING'] = False
    with app.test_client() as client:
        # Try to access overview, should redirect to login
        response = client.get('/overview')
        assert response.status_code == 302
        assert '/login' in response.headers['Location']
        
        # Access login, should be 200
        response = client.get('/login')
        assert response.status_code == 200
        
        # Access static, should be 404 (or whatever if it doesn't exist, but not redirected)
        response = client.get('/static/nonexistent')
        assert response.status_code != 302

    print("Login redirection tests passed!")

if __name__ == "__main__":
    test_login_redirection()
