#!/usr/bin/env python3
"""
Invoice Reminder Bot
Automated follow-up for unpaid invoices
"""

import os
import sys
import argparse
import json
import smtplib
import time
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Dict, List, Optional
from dotenv import load_dotenv
import schedule

try:
    import stripe
    STRIPE_AVAILABLE = True
except ImportError:
    STRIPE_AVAILABLE = False

load_dotenv()

class InvoiceReminderBot:
    def __init__(self):
        self.stripe_key = os.getenv('STRIPE_SECRET_KEY')
        if STRIPE_AVAILABLE and self.stripe_key:
            stripe.api_key = self.stripe_key
        
        # Email settings
        self.smtp_host = os.getenv('SMTP_HOST', 'smtp.gmail.com')
        self.smtp_port = int(os.getenv('SMTP_PORT', 587))
        self.smtp_user = os.getenv('SMTP_USER')
        self.smtp_password = os.getenv('SMTP_PASSWORD')
        
        # Reminder settings
        reminder_days_str = os.getenv('REMINDER_DAYS', '7,14,21')
        self.reminder_days = [int(d) for d in reminder_days_str.split(',')]
        self.max_reminders = int(os.getenv('MAX_REMINDERS', 3))
        
        self.state_file = 'invoice_state.json'
        self.state = self.load_state()
    
    def load_state(self) -> Dict:
        """Load invoice reminder state"""
        if os.path.exists(self.state_file):
            with open(self.state_file, 'r') as f:
                return json.load(f)
        return {'invoices': {}}
    
    def save_state(self):
        """Save invoice reminder state"""
        with open(self.state_file, 'w') as f:
            json.dump(self.state, f, indent=2)
    
    def get_unpaid_invoices(self) -> List[Dict]:
        """Get unpaid invoices from Stripe"""
        if not STRIPE_AVAILABLE or not self.stripe_key:
            print("⚠️  Stripe not configured")
            return []
        
        try:
            invoices = stripe.Invoice.list(
                status='open',
                limit=100
            )
            
            unpaid = []
            for invoice in invoices.data:
                if invoice.amount_due > 0:
                    unpaid.append({
                        'id': invoice.id,
                        'customer_id': invoice.customer,
                        'amount_due': invoice.amount_due / 100,  # Convert from cents
                        'currency': invoice.currency.upper(),
                        'due_date': invoice.due_date,
                        'customer_email': invoice.customer_email,
                        'number': invoice.number,
                        'created': invoice.created
                    })
            
            return unpaid
        except Exception as e:
            print(f"❌ Error fetching invoices: {e}")
            return []
    
    def get_customer_info(self, customer_id: str) -> Optional[Dict]:
        """Get customer information"""
        if not STRIPE_AVAILABLE or not self.stripe_key:
            return None
        
        try:
            customer = stripe.Customer.retrieve(customer_id)
            return {
                'email': customer.email,
                'name': customer.name,
                'id': customer.id
            }
        except Exception as e:
            print(f"❌ Error fetching customer: {e}")
            return None
    
    def send_reminder_email(self, invoice: Dict, reminder_number: int) -> bool:
        """Send reminder email for unpaid invoice"""
        if not self.smtp_user or not self.smtp_password:
            print("⚠️  Email not configured")
            return False
        
        customer_email = invoice.get('customer_email')
        if not customer_email:
            customer_info = self.get_customer_info(invoice['customer_id'])
            if customer_info:
                customer_email = customer_info['email']
        
        if not customer_email:
            print(f"⚠️  No email for invoice {invoice['id']}")
            return False
        
        try:
            msg = MIMEMultipart('alternative')
            msg['From'] = self.smtp_user
            msg['To'] = customer_email
            msg['Subject'] = f"Reminder: Payment Due for Invoice {invoice.get('number', invoice['id'])}"
            
            due_date = datetime.fromtimestamp(invoice['due_date']) if invoice.get('due_date') else None
            days_overdue = (datetime.now() - due_date).days if due_date else 0
            
            text = f"""
            Hello,
            
            This is a friendly reminder that payment is due for Invoice {invoice.get('number', invoice['id'])}.
            
            Amount Due: {invoice['currency']} {invoice['amount_due']:.2f}
            {'Days Overdue: ' + str(days_overdue) if days_overdue > 0 else ''}
            
            Please make payment at your earliest convenience.
            
            Thank you,
            Invoice Reminder Bot
            """
            
            html = f"""
            <html>
              <body>
                <p>Hello,</p>
                <p>This is a friendly reminder that payment is due for Invoice <strong>{invoice.get('number', invoice['id'])}</strong>.</p>
                <p><strong>Amount Due:</strong> {invoice['currency']} {invoice['amount_due']:.2f}</p>
                {f'<p><strong>Days Overdue:</strong> {days_overdue}</p>' if days_overdue > 0 else ''}
                <p>Please make payment at your earliest convenience.</p>
                <p>Thank you,<br>Invoice Reminder Bot</p>
              </body>
            </html>
            """
            
            part1 = MIMEText(text, 'plain')
            part2 = MIMEText(html, 'html')
            
            msg.attach(part1)
            msg.attach(part2)
            
            server = smtplib.SMTP(self.smtp_host, self.smtp_port)
            server.starttls()
            server.login(self.smtp_user, self.smtp_password)
            server.send_message(msg)
            server.quit()
            
            return True
        except Exception as e:
            print(f"❌ Error sending email: {e}")
            return False
    
    def check_and_remind(self):
        """Check for unpaid invoices and send reminders"""
        print("🔍 Checking for unpaid invoices...")
        
        unpaid_invoices = self.get_unpaid_invoices()
        print(f"Found {len(unpaid_invoices)} unpaid invoice(s)")
        
        for invoice in unpaid_invoices:
            invoice_id = invoice['id']
            due_date = datetime.fromtimestamp(invoice['due_date']) if invoice.get('due_date') else None
            
            if not due_date:
                continue
            
            days_since_due = (datetime.now() - due_date).days
            
            # Initialize invoice state if needed
            if invoice_id not in self.state['invoices']:
                self.state['invoices'][invoice_id] = {
                    'reminders_sent': 0,
                    'last_reminder': None
                }
            
            invoice_state = self.state['invoices'][invoice_id]
            reminders_sent = invoice_state['reminders_sent']
            
            # Check if reminder should be sent
            should_remind = False
            reminder_number = 0
            
            for i, days in enumerate(self.reminder_days, 1):
                if days_since_due >= days and reminders_sent < i and reminders_sent < self.max_reminders:
                    should_remind = True
                    reminder_number = i
                    break
            
            if should_remind:
                print(f"📧 Sending reminder #{reminder_number} for invoice {invoice.get('number', invoice_id)}")
                
                if self.send_reminder_email(invoice, reminder_number):
                    invoice_state['reminders_sent'] = reminder_number
                    invoice_state['last_reminder'] = datetime.now().isoformat()
                    self.save_state()
                    print(f"✅ Reminder sent")
                else:
                    print(f"❌ Failed to send reminder")
            else:
                if reminders_sent >= self.max_reminders:
                    print(f"⏭️  Max reminders reached for invoice {invoice.get('number', invoice_id)}")
                elif days_since_due < min(self.reminder_days):
                    print(f"⏭️  Too early to remind for invoice {invoice.get('number', invoice_id)}")
    
    def list_unpaid(self):
        """List all unpaid invoices"""
        unpaid = self.get_unpaid_invoices()
        
        if not unpaid:
            print("✅ No unpaid invoices")
            return
        
        print("\n" + "="*80)
        print("UNPAID INVOICES")
        print("="*80)
        
        for invoice in unpaid:
            due_date = datetime.fromtimestamp(invoice['due_date']) if invoice.get('due_date') else None
            days_overdue = (datetime.now() - due_date).days if due_date else 0
            
            reminders_sent = self.state['invoices'].get(invoice['id'], {}).get('reminders_sent', 0)
            
            print(f"\nInvoice: {invoice.get('number', invoice['id'])}")
            print(f"  Amount: {invoice['currency']} {invoice['amount_due']:.2f}")
            print(f"  Customer: {invoice.get('customer_email', 'N/A')}")
            if due_date:
                print(f"  Due Date: {due_date.strftime('%Y-%m-%d')} ({days_overdue} days overdue)")
            print(f"  Reminders Sent: {reminders_sent}/{self.max_reminders}")
    
    def send_manual_reminder(self, invoice_id: str):
        """Send manual reminder for specific invoice"""
        unpaid = self.get_unpaid_invoices()
        invoice = next((inv for inv in unpaid if inv['id'] == invoice_id), None)
        
        if not invoice:
            print(f"❌ Invoice {invoice_id} not found or already paid")
            return
        
        invoice_state = self.state['invoices'].get(invoice_id, {'reminders_sent': 0})
        reminder_number = invoice_state['reminders_sent'] + 1
        
        print(f"📧 Sending manual reminder for invoice {invoice.get('number', invoice_id)}")
        
        if self.send_reminder_email(invoice, reminder_number):
            invoice_state['reminders_sent'] = reminder_number
            invoice_state['last_reminder'] = datetime.now().isoformat()
            self.state['invoices'][invoice_id] = invoice_state
            self.save_state()
            print("✅ Reminder sent")
        else:
            print("❌ Failed to send reminder")
    
    def run_continuous(self, interval: int = 3600):
        """Run continuous monitoring"""
        print(f"🚀 Starting invoice reminder bot (checking every {interval}s)")
        
        schedule.every(interval).seconds.do(self.check_and_remind)
        
        # Initial check
        self.check_and_remind()
        
        while True:
            schedule.run_pending()
            time.sleep(60)


def main():
    parser = argparse.ArgumentParser(description='Invoice Reminder Bot')
    parser.add_argument('--check-once', action='store_true', help='Check once and exit')
    parser.add_argument('--list-unpaid', action='store_true', help='List unpaid invoices')
    parser.add_argument('--remind', help='Send manual reminder for invoice ID')
    parser.add_argument('--interval', type=int, default=3600, help='Check interval in seconds')
    
    args = parser.parse_args()
    
    try:
        bot = InvoiceReminderBot()
        
        if args.list_unpaid:
            bot.list_unpaid()
        elif args.remind:
            bot.send_manual_reminder(args.remind)
        elif args.check_once:
            bot.check_and_remind()
        else:
            bot.run_continuous(args.interval)
    except Exception as e:
        print(f"❌ Error: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()


