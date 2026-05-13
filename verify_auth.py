import json
from auth import create_default_admin, User, load_user
from db import r
from werkzeug.security import check_password_hash

def test_auth():
    # Clear existing admin if any
    r.delete("user:admin")
    
    # Test create_default_admin
    create_default_admin()
    assert r.exists("user:admin")
    
    user_data_raw = r.get("user:admin")
    user_data = json.loads(user_data_raw)
    assert user_data['username'] == 'admin'
    assert check_password_hash(user_data['password'], 'admin')
    
    # Test load_user
    user = load_user('admin')
    assert user is not None
    assert user.id == 'admin'
    
    user_none = load_user('nonexistent')
    assert user_none is None
    
    print("Auth logic tests passed!")

if __name__ == "__main__":
    test_auth()
