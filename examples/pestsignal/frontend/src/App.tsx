import React from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { AuthProvider, useAuth } from './context/AuthContext';
import { ToastProvider } from './context/ToastContext';

// Layouts
import { FarmerLayout } from './components/layouts/FarmerLayout';
import { AdminLayout } from './components/layouts/AdminLayout';

// Public Pages
import { LandingPage } from './pages/public/LandingPage';
import { LoginPage } from './pages/public/LoginPage';
import { RegisterPage } from './pages/public/RegisterPage';
import { ForgotPasswordPage } from './pages/public/ForgotPasswordPage';

// Farmer Pages
import { FarmerDashboard } from './pages/farmer/FarmerDashboard';
import { SubmitCropReport } from './pages/farmer/SubmitCropReport';
import { MyReports } from './pages/farmer/MyReports';
import { ReportDetails } from './pages/farmer/ReportDetails';
import { NotificationsPage } from './pages/farmer/NotificationsPage';
import { FarmerProfile } from './pages/farmer/FarmerProfile';
import { FarmerSettings } from './pages/farmer/FarmerSettings';

// Admin Pages
import { AdminDashboard } from './pages/admin/AdminDashboard';
import { AllReportsPage } from './pages/admin/AllReportsPage';
import { ReportInvestigationPage } from './pages/admin/ReportInvestigationPage';
import { OutbreakMonitoringPage } from './pages/admin/OutbreakMonitoringPage';
import { RegionalAnalyticsPage } from './pages/admin/RegionalAnalyticsPage';
import { MapViewPage } from './pages/admin/MapViewPage';
import { AlertsManagementPage } from './pages/admin/AlertsManagementPage';
import { UserManagementPage } from './pages/admin/UserManagementPage';
import { AdminSettingsPage } from './pages/admin/AdminSettingsPage';

// Protected Route Guard with Role Check
const ProtectedRoute: React.FC<{
  allowedRoles?: ('farmer' | 'officer' | 'admin')[];
  children: React.ReactNode;
}> = ({ allowedRoles, children }) => {
  const { user, isLoading } = useAuth();

  if (isLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-[#F7F9F6] text-sm text-[#738076]">
        Authenticating session...
      </div>
    );
  }

  if (!user) {
    return <Navigate to="/login" replace />;
  }

  if (allowedRoles && !allowedRoles.includes(user.role)) {
    // If a farmer attempts to access administrative functions, redirect to farmer dashboard
    if (user.role === 'farmer') {
      return <Navigate to="/farmer/dashboard" replace />;
    }
    return <Navigate to="/admin/dashboard" replace />;
  }

  return <>{children}</>;
};

export function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <ToastProvider>
          <Routes>
            {/* Public Pages */}
            <Route path="/" element={<LandingPage />} />
            <Route path="/login" element={<LoginPage />} />
            <Route path="/register" element={<RegisterPage />} />
            <Route path="/forgot-password" element={<ForgotPasswordPage />} />

            {/* Farmer Platform (Protected) */}
            <Route
              path="/farmer"
              element={
                <ProtectedRoute allowedRoles={['farmer', 'officer', 'admin']}>
                  <FarmerLayout />
                </ProtectedRoute>
              }
            >
              <Route index element={<Navigate to="/farmer/dashboard" replace />} />
              <Route path="dashboard" element={<FarmerDashboard />} />
              <Route path="submit" element={<SubmitCropReport />} />
              <Route path="reports" element={<MyReports />} />
              <Route path="reports/:id" element={<ReportDetails />} />
              <Route path="notifications" element={<NotificationsPage />} />
              <Route path="profile" element={<FarmerProfile />} />
              <Route path="settings" element={<FarmerSettings />} />
            </Route>

            {/* Admin / Officer Platform (Protected: Officers & Admins only) */}
            <Route
              path="/admin"
              element={
                <ProtectedRoute allowedRoles={['officer', 'admin']}>
                  <AdminLayout />
                </ProtectedRoute>
              }
            >
              <Route index element={<Navigate to="/admin/dashboard" replace />} />
              <Route path="dashboard" element={<AdminDashboard />} />
              <Route path="reports" element={<AllReportsPage />} />
              <Route path="reports/:id" element={<ReportInvestigationPage />} />
              <Route path="investigations" element={<AllReportsPage />} />
              <Route path="outbreaks" element={<OutbreakMonitoringPage />} />
              <Route path="analytics" element={<RegionalAnalyticsPage />} />
              <Route path="map" element={<MapViewPage />} />
              <Route path="alerts" element={<AlertsManagementPage />} />
              <Route path="users" element={<UserManagementPage />} />
              <Route path="settings" element={<AdminSettingsPage />} />
            </Route>

            {/* Catch-all Redirect */}
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </ToastProvider>
      </AuthProvider>
    </BrowserRouter>
  );
}

export default App;
