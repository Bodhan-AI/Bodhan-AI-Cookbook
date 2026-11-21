import React, { useState, useEffect } from 'react';
import { Users, Shield, UserCheck, Calendar } from 'lucide-react';
import { userApi } from '../../services/api';
import { User } from '../../types';
import { useToast } from '../../context/ToastContext';

export const UserManagementPage: React.FC = () => {
  const { showToast } = useToast();
  const [users, setUsers] = useState<User[]>([]);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    loadUsers();
  }, []);

  const loadUsers = async () => {
    setIsLoading(true);
    try {
      const data = await userApi.list();
      setUsers(data);
    } catch (err) {
      console.error('Failed to load users', err);
    } finally {
      setIsLoading(false);
    }
  };

  const handleRoleChange = async (userId: number, newRole: string) => {
    try {
      await userApi.updateRole(userId, newRole);
      showToast('success', 'Role Updated', `User role changed to ${newRole}`);
      loadUsers();
    } catch (err: any) {
      showToast('error', 'Error', err.message || 'Could not change user role.');
    }
  };

  return (
    <div className="space-y-6 pb-12">
      <div>
        <h1 className="text-2xl sm:text-3xl font-black text-[#174D35] tracking-tight">User Management</h1>
        <p className="text-xs sm:text-sm text-[#738076]">
          View and manage registered farmers, extension officers, and platform administrators.
        </p>
      </div>

      <div className="bg-white rounded-3xl border border-gray-200/80 shadow-xs overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="bg-[#F7F9F6] text-[#738076] font-bold uppercase tracking-wider border-b border-gray-200">
              <tr>
                <th className="py-3.5 px-4">User</th>
                <th className="py-3.5 px-4">Email</th>
                <th className="py-3.5 px-4">Phone</th>
                <th className="py-3.5 px-4">Region</th>
                <th className="py-3.5 px-4">Language</th>
                <th className="py-3.5 px-4">Role</th>
                <th className="py-3.5 px-4 text-right">Change Role</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {users.map((u) => (
                <tr key={u.id} className="hover:bg-gray-50/80 transition-colors">
                  <td className="py-3.5 px-4 font-bold text-gray-900">{u.full_name}</td>
                  <td className="py-3.5 px-4 text-gray-600">{u.email}</td>
                  <td className="py-3.5 px-4 text-gray-500">{u.phone || '—'}</td>
                  <td className="py-3.5 px-4 text-gray-700">{u.district || '—'}, {u.state || '—'}</td>
                  <td className="py-3.5 px-4 uppercase font-semibold text-gray-600">{u.preferred_language}</td>
                  <td className="py-3.5 px-4">
                    <span className="px-2 py-0.5 rounded-full font-bold text-[10px] bg-emerald-100 text-[#174D35] uppercase">
                      {u.role}
                    </span>
                  </td>
                  <td className="py-3.5 px-4 text-right">
                    <select
                      value={u.role}
                      onChange={(e) => handleRoleChange(u.id, e.target.value)}
                      className="px-2 py-1 rounded border border-gray-300 text-xs bg-white font-semibold"
                    >
                      <option value="farmer">Farmer</option>
                      <option value="officer">Officer</option>
                      <option value="admin">Admin</option>
                    </select>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
};
