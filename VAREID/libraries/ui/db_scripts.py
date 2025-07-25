import sqlite3
import time
import threading
import atexit
import uuid
import os
from datetime import datetime, timedelta

# Unique instance ID for this process
INSTANCE_ID = str(uuid.uuid4())[:8]
PROCESS_ID = os.getpid()
INSTANCE_IDENTIFIER = f"{INSTANCE_ID}-{PROCESS_ID}"

# Global tracking for this instance's active pairs only
instance_active_pairs = set()
cleanup_thread = None
shutdown_flag = threading.Event()

def init_db(db_path="./zebra_verification.db"):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS image_verification (
        id TEXT PRIMARY KEY,
        uuid1 TEXT,
        image1_path TEXT,
        bbox1 TEXT,
        cluster1 TEXT,
        uuid2 TEXT,
        image2_path TEXT,
        bbox2 TEXT,
        cluster2 TEXT,
        status TEXT CHECK(status IN ('awaiting', 'in_progress', 'checked', 'sent')) DEFAULT 'awaiting',
        decision TEXT CHECK(decision IN ('none', 'correct', 'incorrect', 'cant_tell')) DEFAULT 'none',
        started_at TIMESTAMP,
        completed_at TIMESTAMP,
        instance_id TEXT,
        heartbeat TIMESTAMP
    )
    """)
    conn.commit()
    conn.close()
    
    # Reset any pairs that belonged to this instance (in case of restart)
    reset_instance_pairs(db_path)


def add_image_pairs(pairs, db_path="./zebra_verification.db"):
    """Batch insert image pairs. pairs is a list of tuples: [(id, uuid1, path1, bbox1, uuid2, path2, bbox2), ...]"""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.executemany("""
        INSERT OR IGNORE INTO image_verification (id, uuid1, image1_path, bbox1, cluster1, uuid2, image2_path, bbox2, cluster2, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'awaiting')
    """, pairs)
    inserted_count = cursor.rowcount
    conn.commit()
    conn.close()
    if inserted_count > 0:
        print(f"Added {inserted_count} image pair(s) successfully.")
    return inserted_count


def add_image_pair(id, uuid1, image1_path, bbox1, cluster1, uuid2, image2_path, bbox2, cluster2, db_path="./zebra_verification.db"):
    """Add a single image pair - calls batch function with one item"""
    return add_image_pairs([(id, uuid1, image1_path, bbox1, cluster1, uuid2, image2_path, bbox2, cluster2)], db_path)


def get_decisions(pair_ids, db_path="./zebra_verification.db"):
    """Get decisions for multiple pairs and mark them as sent. Returns dict: {pair_id: decision}"""
    if not pair_ids:
        return {}
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Create placeholders for SQL IN clause
    placeholders = ','.join('?' * len(pair_ids))
    
    # Get decisions
    cursor.execute(f"""
        SELECT id, decision FROM image_verification
        WHERE id IN ({placeholders}) AND status = 'checked'
    """, pair_ids)
    
    results = cursor.fetchall()
    
    if results:
        # Mark as sent
        cursor.execute(f"""
            UPDATE image_verification SET status = 'sent'
            WHERE id IN ({placeholders}) AND status = 'checked'
        """, pair_ids)
    
    conn.commit()
    conn.close()
    
    return {pair_id: decision for pair_id, decision in results}


def get_decision(pair_id, db_path="./zebra_verification.db"):
    """Get decision for one pair - calls batch function with one item"""
    results = get_decisions([pair_id], db_path)
    return results.get(pair_id)


def get_existing_pair_decision(uuid1, uuid2, db_path="./zebra_verification.db"):
    """Check if a pair with these UUIDs already exists and has been decided.
    Checks both UUID orderings since pairs can be submitted in either order.
    Returns decision if found and checked/sent, None otherwise."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Check both possible orderings
    cursor.execute("""
        SELECT decision FROM image_verification
        WHERE ((uuid1 = ? AND uuid2 = ?) OR (uuid1 = ? AND uuid2 = ?))
        AND status IN ('checked', 'sent')
        LIMIT 1
    """, (uuid1, uuid2, uuid2, uuid1))
    
    result = cursor.fetchone()
    conn.close()
    
    return result[0] if result else None


def check_pair_exists(uuid1, uuid2, db_path="./zebra_verification.db"):
    """Check if a pair with these UUIDs exists in any status.
    Returns (exists, status, decision) tuple."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Check both possible orderings
    cursor.execute("""
        SELECT status, decision FROM image_verification
        WHERE ((uuid1 = ? AND uuid2 = ?) OR (uuid1 = ? AND uuid2 = ?))
        LIMIT 1
    """, (uuid1, uuid2, uuid2, uuid1))
    
    result = cursor.fetchone()
    conn.close()
    
    if result:
        return (True, result[0], result[1])
    else:
        return (False, None, None)


def reset_instance_pairs(db_path="./zebra_verification.db"):
    """Reset pairs that belonged to this specific instance"""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    cursor.execute("""
        UPDATE image_verification 
        SET status='awaiting', started_at=NULL, instance_id=NULL, heartbeat=NULL
        WHERE status='in_progress' AND instance_id=?
    """, (INSTANCE_IDENTIFIER,))
    
    reset_count = cursor.rowcount
    if reset_count > 0:
        print(f"Reset {reset_count} pairs from previous instance {INSTANCE_IDENTIFIER}")
    
    conn.commit()
    conn.close()
    return reset_count


def reset_stale_pairs(db_path="./zebra_verification.db", timeout_minutes=5):
    """Reset pairs that haven't had a heartbeat update in timeout_minutes"""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    timeout_time = datetime.now() - timedelta(minutes=timeout_minutes)
    cursor.execute("""
        UPDATE image_verification 
        SET status='awaiting', started_at=NULL, instance_id=NULL, heartbeat=NULL
        WHERE status='in_progress' 
        AND (heartbeat IS NULL OR heartbeat < ?)
    """, (timeout_time,))
    
    reset_count = cursor.rowcount
    if reset_count > 0:
        print(f"Reset {reset_count} stale pairs (no heartbeat for >{timeout_minutes} min)")
    
    conn.commit()
    conn.close()
    return reset_count


def get_next_pair_atomic(db_path="./zebra_verification.db"):
    """Atomically get and reserve the next available pair"""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Use a transaction to atomically get and reserve a pair
    cursor.execute("BEGIN IMMEDIATE")
    
    try:
        # Find the next available pair
        cursor.execute("""
            SELECT id, image1_path, image2_path, bbox1, bbox2, cluster1, cluster2 FROM image_verification
            WHERE status = 'awaiting'
            ORDER BY id ASC LIMIT 1
        """)
        result = cursor.fetchone()
        
        if result:
            pair_id = result[0]
            # Immediately reserve it for this instance
            cursor.execute("""
                UPDATE image_verification 
                SET status='in_progress', started_at=?, instance_id=?, heartbeat=?
                WHERE id=? AND status='awaiting'
            """, (datetime.now(), INSTANCE_IDENTIFIER, datetime.now(), pair_id))
            
            if cursor.rowcount == 1:
                # Successfully reserved
                conn.commit()
                instance_active_pairs.add(pair_id)
                return result
            else:
                # Someone else got it first
                conn.rollback()
                return None
        else:
            conn.rollback()
            return None
            
    except Exception as e:
        conn.rollback()
        print(f"Error in get_next_pair_atomic: {e}")
        return None
    finally:
        conn.close()


def update_heartbeat(pair_id, db_path="./zebra_verification.db"):
    """Update heartbeat for a pair to show this instance is still working on it"""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE image_verification 
        SET heartbeat=? 
        WHERE id=? AND instance_id=? AND status='in_progress'
    """, (datetime.now(), pair_id, INSTANCE_IDENTIFIER))
    conn.commit()
    conn.close()


def update_status(pair_id, decision, db_path="./zebra_verification.db"):
    """Update pair status to completed (only if owned by this instance)"""
    global instance_active_pairs
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE image_verification 
        SET status='checked', decision=?, completed_at=?
        WHERE id=? AND instance_id=? AND status='in_progress'
    """, (decision, datetime.now(), pair_id, INSTANCE_IDENTIFIER))
    
    if cursor.rowcount == 1:
        conn.commit()
        instance_active_pairs.discard(pair_id)
        success = True
    else:
        conn.rollback()
        print(f"Warning: Could not update pair {pair_id} - may have been taken by another instance")
        success = False
    
    conn.close()
    return success


def release_pair(pair_id, db_path="./zebra_verification.db"):
    """Release a pair back to awaiting status (only if owned by this instance)"""
    global instance_active_pairs
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE image_verification 
        SET status='awaiting', started_at=NULL, instance_id=NULL, heartbeat=NULL
        WHERE id=? AND instance_id=? AND status='in_progress'
    """, (pair_id, INSTANCE_IDENTIFIER))
    
    if cursor.rowcount == 1:
        conn.commit()
        instance_active_pairs.discard(pair_id)
        success = True
    else:
        conn.rollback()
        success = False
    
    conn.close()
    return success


def cleanup_instance_pairs(db_path="./zebra_verification.db"):
    """Clean up any pairs that this instance was working on"""
    global instance_active_pairs
    
    if instance_active_pairs:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Reset all pairs this instance was working on
        cursor.execute("""
            UPDATE image_verification 
            SET status='awaiting', started_at=NULL, instance_id=NULL, heartbeat=NULL
            WHERE instance_id=? AND status='in_progress'
        """, (INSTANCE_IDENTIFIER,))
        
        reset_count = cursor.rowcount
        if reset_count > 0:
            print(f"Instance {INSTANCE_IDENTIFIER} cleaned up {reset_count} active pairs on shutdown")
        
        conn.commit()
        conn.close()
        instance_active_pairs.clear()


def heartbeat_worker(db_path="./zebra_verification.db"):
    """Background worker to update heartbeats and clean up stale pairs"""
    global shutdown_flag, instance_active_pairs
    
    while not shutdown_flag.wait(30):  # Update every 30 seconds
        try:
            # Update heartbeats for our active pairs
            if instance_active_pairs:
                conn = sqlite3.connect(db_path)
                cursor = conn.cursor()
                
                # Update heartbeat for all our active pairs
                placeholders = ','.join('?' * len(instance_active_pairs))
                cursor.execute(f"""
                    UPDATE image_verification 
                    SET heartbeat=? 
                    WHERE id IN ({placeholders}) AND instance_id=? AND status='in_progress'
                """, [datetime.now()] + list(instance_active_pairs) + [INSTANCE_IDENTIFIER])
                
                conn.commit()
                conn.close()
            
            # Clean up stale pairs from other instances
            reset_stale_pairs(db_path, timeout_minutes=3)
            
        except Exception as e:
            print(f"Error in heartbeat worker: {e}")


def start_heartbeat_system(db_path="./zebra_verification.db"):
    """Start the heartbeat system"""
    global cleanup_thread, shutdown_flag
    
    cleanup_thread = threading.Thread(target=heartbeat_worker, args=(db_path,), daemon=True)
    cleanup_thread.start()
    print(f"Started heartbeat system for instance {INSTANCE_IDENTIFIER}")


def stop_heartbeat_system():
    """Stop the heartbeat system"""
    global shutdown_flag
    shutdown_flag.set()


def get_instance_stats(db_path="./zebra_verification.db"):
    """Get statistics about pairs by instance"""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT 
            COUNT(*) as total,
            SUM(CASE WHEN status='awaiting' THEN 1 ELSE 0 END) as awaiting,
            SUM(CASE WHEN status='in_progress' THEN 1 ELSE 0 END) as in_progress,
            SUM(CASE WHEN status='checked' THEN 1 ELSE 0 END) as checked,
            COUNT(DISTINCT instance_id) as active_instances
        FROM image_verification
    """)
    
    stats = cursor.fetchone()
    
    cursor.execute("""
        SELECT instance_id, COUNT(*) as pairs_count
        FROM image_verification 
        WHERE status='in_progress' AND instance_id IS NOT NULL
        GROUP BY instance_id
    """)
    
    instance_breakdown = cursor.fetchall()
    
    conn.close()
    
    return {
        'total': stats[0],
        'awaiting': stats[1], 
        'in_progress': stats[2],
        'checked': stats[3],
        'active_instances': stats[4],
        'instance_breakdown': instance_breakdown,
        'current_instance': INSTANCE_IDENTIFIER
    }


# Register cleanup functions to run on exit
atexit.register(cleanup_instance_pairs)
atexit.register(stop_heartbeat_system)